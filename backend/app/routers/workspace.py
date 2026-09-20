"""Dataset workspace endpoints: case files, saved analysis, rate cards, repairs,
reports and the active-dataset preference.

Everything here is dataset-scoped by an explicit ``key`` query parameter. The
router never guesses a dataset: a request for a key that is not loaded returns
404, so a page can never silently render another dataset's numbers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response

from ..schemas import CostRateCard
from ..services import analysis, repairs, reports, workspace
from ..services.catalog import catalog

log = logging.getLogger("ftm.workspace")

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _check(key: str) -> str:
    if not key:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please upload a dataset to begin.")
    if key not in catalog.frames:
        record = workspace.find_record(key)
        if record and not record.get("present"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{record['name']}' is registered with its saved history, but its source file is missing, "
                    "so it cannot be analysed. Re-upload the CSV to restore it."
                ),
            )
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    return key


def _refresh_presence() -> None:
    """Make the case-file switcher agree with the files on disk before it is read.

    Cheap by design: it touches only the presence flag and its label, and only when
    they disagree. Doing it here is what makes a wrongly flagged dataset recover on
    the next page load instead of staying "missing" until someone restarts the app.
    """
    try:
        workspace.reconcile_presence(catalog if catalog.is_ready() else None)
    except Exception as exc:  # pragma: no cover - never fail a page over a flag
        log.warning("presence reconciliation failed: %s", exc)


def _default_key() -> str:
    """Never default to a built-in archive: prefer the newest uploaded dataset."""
    records = [r for r in workspace.list_records() if r["present"]]
    uploads = [r for r in records if r["key"].startswith("user:")]
    if uploads:
        return uploads[0]["key"]
    return records[0]["key"] if records else ""


def _saved_rate_card(key: str) -> Optional[CostRateCard]:
    stored = workspace.load_rate_card(key)
    if not stored:
        return None
    try:
        return CostRateCard(**(stored.get("rates") or {}))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("stored rate card for %s is unreadable: %s", key, exc)
        return None


# ---------------------------------------------------------------------------
# Registry / history
# ---------------------------------------------------------------------------
@router.get("/datasets")
def datasets() -> Dict[str, Any]:
    """The dataset history: every case file, newest first, with what it holds."""
    _refresh_presence()
    records = workspace.list_records()
    active = workspace.get_active_key(_default_key())
    return {
        "active": active,
        "count": len(records),
        "datasets": records,
        "note": (
            "Each dataset keeps its own analysis, rate card, scenarios, AI findings and feedback. "
            "Uploading a new dataset never replaces an older one."
        ),
    }


@router.get("/active")
def get_active() -> Dict[str, Any]:
    return {"active": workspace.get_active_key(_default_key())}


@router.post("/active")
def set_active(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    key = str(payload.get("key") or "")
    if not key:
        raise HTTPException(status_code=422, detail="a dataset key is required")
    if workspace.find_record(key) is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    if key not in catalog.frames:
        raise HTTPException(
            status_code=409,
            detail="that dataset's source file is missing, so it cannot be made active",
        )
    return {"active": workspace.set_active_key(key)}


@router.get("/datasets/{key:path}")
def dataset_context(key: str) -> Dict[str, Any]:
    """One case file's header plus its saved history."""
    _refresh_presence()
    record = workspace.find_record(key)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    context = workspace.context(record["key"])
    context["loaded"] = record["key"] in catalog.frames
    return context


@router.delete("/datasets/{key:path}")
def delete_dataset(key: str) -> Dict[str, Any]:
    """Remove an uploaded dataset's source file. Its history is retained."""
    record = workspace.find_record(key)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    if not record["key"].startswith("user:"):
        raise HTTPException(
            status_code=400,
            detail="the supplied archives are part of the project and cannot be deleted from the workspace",
        )
    path = workspace.uploaded_file_path(record["key"])
    removed = False
    if path is not None:
        try:
            path.unlink()
            removed = True
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"could not remove '{path.name}': {exc}") from exc
    workspace.mark_source_removed(record["key"])
    catalog.build()
    return {
        "deleted": record["key"],
        "file_removed": removed,
        "history_retained": True,
        "note": (
            "The CSV was removed and the dataset is now flagged 'source file missing'. Its saved analysis, "
            "scenarios and feedback remain in the case file."
        ),
    }


# ---------------------------------------------------------------------------
# Saved analysis
# ---------------------------------------------------------------------------
@router.post("/analysis/run")
def run_analysis(
    key: str = Query(...),
    rates: Optional[CostRateCard] = Body(default=None),
    use_saved_rates: bool = Query(True, description="use this dataset's stored rate card unless rates are supplied"),
    include_ai: bool = Query(True),
) -> Dict[str, Any]:
    _check(key)
    card = rates or (_saved_rate_card(key) if use_saved_rates else None)
    return analysis.run_analysis(catalog, key, rate_card=card, include_ai=include_ai, persist=True)


@router.get("/analysis")
def latest_analysis(key: str = Query(...)) -> Dict[str, Any]:
    _check(key)
    stored = workspace.latest_analysis(key)
    if stored is None:
        return {
            "status": "not_run",
            "key": key,
            "reason": (
                "No analysis has been saved for this dataset yet. Press 'Run analysis' to compute and store its "
                "results (defects, anomalies, bottlenecks, economics, repairs and the AI finding)."
            ),
        }
    return {"status": "saved", **stored}


# ---------------------------------------------------------------------------
# Rate cards (per dataset)
# ---------------------------------------------------------------------------
@router.get("/rate-card")
def get_rate_card(key: str = Query(...)) -> Dict[str, Any]:
    _check(key)
    stored = workspace.load_rate_card(key)
    return {
        "key": key,
        "rate_card": (stored or {}).get("rates") if stored else None,
        "engineer": (stored or {}).get("engineer"),
        "updated_at": (stored or {}).get("updated_at"),
        "note": (
            "A rate card belongs to this dataset only. The same rates are never applied silently to another "
            "dataset - copy them explicitly if that is what you want."
        ),
    }


@router.post("/rate-card")
def save_rate_card(
    key: str = Query(...),
    rates: CostRateCard = Body(...),
    engineer: str = Query("anonymous"),
) -> Dict[str, Any]:
    _check(key)
    stored = workspace.save_rate_card(key, rates.model_dump(), engineer=engineer)
    return {"key": key, "rate_card": stored.get("rates"), "saved": True, "updated_at": stored.get("updated_at")}


@router.delete("/rate-card")
def delete_rate_card(key: str = Query(...)) -> Dict[str, Any]:
    _check(key)
    removed = workspace.delete_rate_card(key)
    return {"key": key, "deleted": removed}


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------
@router.get("/repairs")
def repair_options(key: str = Query(...)) -> Dict[str, Any]:
    _check(key)
    return repairs.repair_options(catalog, key, _saved_rate_card(key))


@router.post("/repairs")
def repair_options_with_rates(
    key: str = Query(...),
    rates: Optional[CostRateCard] = Body(default=None),
) -> Dict[str, Any]:
    _check(key)
    return repairs.repair_options(catalog, key, rates or _saved_rate_card(key))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@router.get("/report")
def report(
    key: str = Query(...),
    format: str = Query("json", pattern="^(json|md|csv)$"),
    refresh: bool = Query(False, description="recompute the analysis instead of using the saved run"),
    download: bool = Query(False, description="send the report as a file attachment"),
) -> Response:
    _check(key)
    payload = reports.build_report(catalog, key, _saved_rate_card(key), refresh=refresh)
    media_type, filename, body = reports.render(payload, format)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else {}
    return Response(content=body, media_type=media_type, headers=headers)


@router.get("/summary")
def analysis_summary(key: str = Query(...)) -> Dict[str, Any]:
    """The compact 'analysis complete' card shown before downloading."""
    _check(key)
    stored = workspace.latest_analysis(key)
    if stored is None:
        return {"status": "not_run", "key": key}
    payload = stored["payload"]
    summary = payload.get("summary", {})
    sections = payload.get("sections", {}) or {}
    repair = (sections.get("repairs") or {}).get("cheapest_supported_effective_repair")
    economic = summary.get("economic", {})
    return {
        #: The four headline cards on the Overview page, read from the *saved* run
        #: rather than re-measured. Without this the cards had to guess, and the
        #: Process card claimed "not supported by this dataset" for a dataset whose
        #: anomaly analysis had in fact run.
        "highlights": {
            "quality": {
                "rows": (sections.get("quality") or {}).get("rows"),
                "missing_cells": (sections.get("quality") or {}).get("missing_cells"),
                "duplicate_rows": (sections.get("quality") or {}).get("duplicate_rows"),
            },
            "process": {
                "available": bool((sections.get("anomaly") or {}).get("available")),
                "anomalous_fraction": (sections.get("anomaly") or {}).get("anomalous_fraction"),
                "reason": (sections.get("anomaly") or {}).get("reason"),
            },
            "production": {
                "available": bool((sections.get("production") or {}).get("available")),
                "bottleneck": (sections.get("production") or {}).get("bottleneck"),
                "reason": (sections.get("production") or {}).get("reason"),
            },
            "economics": {
                "available": bool((sections.get("economics") or {}).get("available")),
                "reason": (sections.get("economics") or {}).get("reason"),
            },
        },
        "status": "complete",
        "dataset": payload.get("dataset", {}),
        "generated_at": payload.get("generated_at"),
        "analysis_run_id": stored["id"],
        "main_issue": (summary.get("quality") or {}).get("main_defect_problem")
        or (summary.get("process") or {}).get("verdict"),
        "repair": (repair or {}).get("repair"),
        "estimated_impact": economic.get("verdict"),
        "estimated_impact_value": economic.get("total"),
        "currency": economic.get("currency"),
        "confidence": summary.get("confidence", {}),
        "formats": [
            {"format": "md", "label": "Final report (Markdown)", "url": f"/api/workspace/report?key={key}&format=md&download=true"},
            {"format": "csv", "label": "Results (CSV)", "url": f"/api/workspace/report?key={key}&format=csv&download=true"},
            {"format": "json", "label": "Full payload (JSON)", "url": f"/api/workspace/report?key={key}&format=json&download=true"},
        ],
        "limitations": summary.get("limitations", []),
    }
