"""Dataset, schema, provenance and linkage endpoints."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from ..schemas import ColumnProfile
from ..services import domain
from ..services.catalog import catalog

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _dataset_overview() -> Dict[str, Any]:
    return {
        "status": catalog.status.snapshot(),
        "datasets": catalog.dataset_summaries(),
        "issues": catalog.issues,
        "linkage": catalog.linkage_report(),
        "capabilities": catalog.get_capabilities(),
        "provenance": catalog.provenance(),
    }


@router.get("/overview")
def overview() -> Dict[str, Any]:
    """Everything the UI needs to render provenance, gating and data quality."""
    return _dataset_overview()


@router.get("/status")
def status() -> Dict[str, Any]:
    """Lightweight progress poll used while the catalog builds."""
    return {"status": catalog.status.snapshot(), "datasets": catalog.dataset_summaries()}


@router.post("/process")
def process(force: bool = Query(False, description="re-read the source files and rebuild the cache")) -> Dict[str, Any]:
    """Kick off (or re-run) dataset processing in the background."""
    catalog.start_background_build(force=force)
    return {"status": catalog.status.snapshot(), "message": "dataset processing started"}


@router.get("/models")
def models() -> List[Dict[str, Any]]:
    """The documented model/station metadata the analytics are built on."""
    out = []
    for key, spec in domain.MODELS.items():
        out.append(
            {
                "key": key,
                "label": spec.label,
                "stages": spec.stages,
                "documented_factors": spec.factors,
                "throughput_columns": spec.throughput_columns,
                "wip_columns": list(spec.wip_columns),
                "notes": spec.notes,
                "stations": [
                    {
                        "key": s.key,
                        "label": s.label,
                        "stage": s.stage,
                        "units": s.units,
                        "capacity": s.capacity,
                        "capacity_source": s.capacity_source,
                        "utilization_column": s.utilization_column,
                        "queue_columns": list(s.queue_columns),
                        "processing_time_seconds": s.processing_time_seconds,
                        "time_source": s.time_source,
                        "time_note": s.time_note,
                    }
                    for s in spec.stations
                ],
            }
        )
    return out


def _check_documented(key: str) -> str:
    """Only the supplied archives have per-column profiles and quality reports.

    An uploaded CSV is profiled by the profiler instead; saying that is kinder
    than "unknown dataset" and stops the UI from requesting a report that does
    not exist for it.
    """
    if key not in domain.MODELS:
        if key.startswith("user:"):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"'{key}' is an uploaded CSV: the profiler reports its structure on the Datasets "
                    "page. Per-column profiles exist for the supplied archives only."
                ),
            )
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    return key


@router.get("/{key}/columns", response_model=List[ColumnProfile])
def columns(key: str, group: str | None = None, documented_only: bool = False) -> List[ColumnProfile]:
    _check_documented(key)
    profiles = catalog.profiles.get(key) or []
    if group:
        profiles = [p for p in profiles if p.group.startswith(group)]
    if documented_only:
        profiles = [p for p in profiles if p.documented]
    return profiles


@router.get("/{key}/quality")
def quality(key: str) -> Dict[str, Any]:
    """Data-quality report: missing cells, duplicates, constants, derived columns."""
    _check_documented(key)
    cache_key = ("quality", key)
    cached = getattr(catalog, "analysis_cache", {}).get(cache_key)
    if cached is not None:
        return cached
    df = catalog.model_df(key)
    issues = [i.model_dump() for i in catalog.issues]
    report = {
        "model_key": key,
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "missing_cells": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
        "constant_columns": [str(c) for c in df.columns if df[c].nunique(dropna=True) <= 1],
        "issues": issues,
        "documented_columns": sum(1 for p in catalog.profiles.get(key, []) if p.documented),
        "undocumented_columns": [
            p.name for p in catalog.profiles.get(key, []) if not p.documented
        ],
    }
    # duplicate() + nunique() over 78 columns x 605k rows is ~4 s cold and the
    # page calls it on every visit; the frame cannot change without a rebuild.
    if hasattr(catalog, "analysis_cache"):
        catalog.analysis_cache[cache_key] = report
    return report


@router.get("/linkage")
def linkage() -> Dict[str, Any]:
    from ..services.economics import ARENA_COSTING_NOT_EXPORTED

    return {
        **catalog.linkage_report(),
        "arena_costing_present_in_model_but_not_exported": ARENA_COSTING_NOT_EXPORTED,
        "arguments": [
            {
                "key": "no_shared_identifier",
                "detail": (
                    "The image archive carries no part, batch or station identifier; the simulation exports "
                    "carry no image reference. There is nothing to join on."
                ),
            },
            {
                "key": "no_timestamps_anywhere",
                "detail": (
                    "Model 3's Time_Now is constant 24 in all 605,620 rows, and Models 1 and 2 export no time "
                    "column at all, so the two domains cannot even be aligned in time."
                ),
            },
            {
                "key": "no_defect_or_cost_columns",
                "detail": (
                    "No export contains a defect, scrap, rework, downtime, cost or currency column, so neither "
                    "quality nor economics can be bridged from the simulation side."
                ),
            },
        ],
    }


@router.get("/design")
def design() -> Dict[str, Any]:
    """The MATLAB surrogate-design arrays and their verified cross-references."""
    return catalog.mat_summary_lazy()


@router.get("/specimens")
def specimens(per_class: int = Query(8, ge=1, le=50)) -> Dict[str, Any]:
    from ..services import vision

    return {
        "classes": catalog.vision_classes,
        "samples": vision.sample_specimens(catalog, per_class=per_class),
        "total_images": len(catalog.vision_index),
        "note": "specimens are identified by class and file index only; the dataset provides no product identifier",
    }
