"""Human-in-the-loop review, dataset linkage assumptions, and AI narrative endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from ..db import FeedbackItem, LinkageAssumption, insert_row, list_rows, session
from ..schemas import AIQuestion, CostRateCard, FeedbackIn, FeedbackOut, LinkageAssumptionIn
from ..services import ai_client, analytics, bottleneck, domain, economics, forensics, repairs, simulation, workspace
from ..services.catalog import catalog

router = APIRouter(prefix="/api", tags=["operations"])


# ---------------------------------------------------------------------------
# Feedback (engineer review)
# ---------------------------------------------------------------------------
@router.post("/review/feedback", response_model=FeedbackOut)
def submit_feedback(payload: FeedbackIn) -> FeedbackOut:
    # The verdict is stored against the dataset whose finding was reviewed, so a
    # later dataset's Review page never shows another case's decisions.
    dataset_key = payload.dataset_key.strip()
    if dataset_key and dataset_key not in catalog.frames and workspace.find_record(dataset_key) is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{dataset_key}'")
    row = insert_row(
        FeedbackItem(
            dataset_key=dataset_key,
            finding_id=payload.finding_id,
            finding_kind=payload.finding_kind,
            finding_title=payload.finding_title[:400],
            payload=__import__("json").dumps(payload.payload, default=str),
            decision=payload.decision,
            note=payload.note[:4000],
            engineer=payload.engineer[:120],
        )
    )
    return FeedbackOut(
        item=row.as_dict(),
        model_retrained=False,
        note=(
            "Stored. This build performs no automatic retraining: nothing in the model pipeline consumes "
            "feedback yet, so no accuracy change should be expected."
        ),
    )


@router.get("/review/feedback")
def list_feedback(
    key: str = Query("", description="only this dataset's reviews"),
    limit: int = Query(100, ge=1, le=500),
) -> List[Dict[str, Any]]:
    rows = [row.as_dict() for row in list_rows(FeedbackItem, limit=limit)]
    if key:
        rows = [r for r in rows if r["dataset_key"] == key]
    return rows


@router.get("/review/summary")
def review_summary(key: str = Query("", description="only this dataset's reviews")) -> Dict[str, Any]:
    items = [row.as_dict() for row in list_rows(FeedbackItem, limit=500)]
    if key:
        items = [r for r in items if r["dataset_key"] == key]
    counts = {"confirmed": 0, "rejected": 0, "needs_review": 0}
    for item in items:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    return {
        "total": len(items),
        "counts": counts,
        "kinds": sorted({i["finding_kind"] for i in items}),
        "retraining": {
            "implemented": False,
            "note": (
                "Feedback is stored for auditing and future supervised retraining, but this build does not "
                "retrain any model from it. Claiming otherwise would be false."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Declared dataset linkage
# ---------------------------------------------------------------------------
@router.get("/review/assumptions")
def list_assumptions() -> Dict[str, Any]:
    rows = [row.as_dict() for row in list_rows(LinkageAssumption, limit=200)]
    return {
        "assumptions": rows,
        "linkage": catalog.linkage_report(),
        "note": (
            "These edges are engineer-declared. They are not derived from the data, because the image archive "
            "and the simulation exports share no key."
        ),
    }


def _known_defect_classes() -> List[str]:
    """The classes the archive actually has, in the order the graph prefers."""
    known = set(catalog.vision_classes)
    ordered = [c for c in ("normal", "crack", "hole", "rust", "scratch") if c in known]
    return ordered + sorted(known - set(ordered))


def _known_station_keys() -> List[str]:
    """Every station key across the models, so one model's station is never rejected."""
    keys: List[str] = []
    for model in domain.MODELS.values():
        for station in model.stations:
            if station.key not in keys:
                keys.append(station.key)
    return keys


@router.post("/review/assumptions")
def create_assumption(payload: LinkageAssumptionIn) -> Dict[str, Any]:
    """Record an engineer-declared defect-class -> station link.

    The values are validated against the datasets first. An assumption naming a
    class or station that does not exist cannot be turned into a graph edge, so
    accepting it would only produce a dangling link later (the audit caught exactly
    that with the class 'crazing', which is not one of the archive labels).
    """
    classes = _known_defect_classes()
    stations = _known_station_keys()
    defect_class = payload.defect_class.strip().lower()
    station = payload.station.strip().lower()
    if defect_class not in classes:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{payload.defect_class}' is not a defect class in the image archive. "
                f"Known classes: {', '.join(classes)}."
            ),
        )
    if station not in stations:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{payload.station}' is not a station in the simulation exports. "
                f"Known stations: {', '.join(stations)}."
            ),
        )
    row = insert_row(
        LinkageAssumption(
            defect_class=defect_class,
            station=station,
            rationale=payload.rationale[:2000],
            author=payload.author[:120],
            active=payload.active,
        )
    )
    return {
        "assumption": row.as_dict(),
        "note": (
            "Recorded as an assumed edge; it is rendered differently in the graph and cannot be verified "
            "from the data, because the image archive and the simulation exports share no key."
        ),
    }


@router.delete("/review/assumptions/{assumption_id}")
def delete_assumption(assumption_id: int) -> Dict[str, Any]:
    with session() as db:
        row = db.get(LinkageAssumption, assumption_id)
        if row is None:
            raise HTTPException(status_code=404, detail="assumption not found")
        db.delete(row)
    return {"deleted": assumption_id}


# ---------------------------------------------------------------------------
# AI narrative
# ---------------------------------------------------------------------------
def _overview_evidence(key: str = "model3") -> Dict[str, Any]:
    """The evidence bundle the AI is given for one dataset.

    Scoped deliberately: whatever is in here is what the model is allowed to say,
    so nothing that belongs to another dataset (or to the supplied archives, when
    the active dataset is an upload) may appear. Every field is derived from this
    dataset's own frame, profile, issues and capability map.
    """
    bottleneck_ranking = bottleneck.rank_bottlenecks(catalog, key)
    top = bottleneck_ranking["ranking"][0] if bottleneck_ranking["ranking"] else {}
    report = analytics.anomaly_report(catalog, key, top=3)
    record = workspace.find_record(key) or {}
    stored_card = workspace.load_rate_card(key)
    capabilities = catalog.capabilities(key)
    # Everything below is scoped to *this* dataset. The evidence is what the model
    # is allowed to talk about, so a fact belonging to another dataset must not be
    # in it: an uploaded CSV's narrative was quoting the supplied archive's missing
    # cells and the simulation export's constant column, i.e. findings the user's
    # data never produced.
    supplied = key in domain.MODELS
    issues = [i.message for i in catalog.issues if i.message.startswith(f"{key}:")]
    profile = catalog.profile_map(key) or {}
    cost_fields = (profile.get("manufacturing") or {}).get("cost_fields") or []
    if supplied:
        columns = len(catalog.profiles.get(key) or [])
    else:
        columns = len(profile.get("columns") or [])
    repair_report: Dict[str, Any] = {}
    try:
        card = CostRateCard(**(stored_card or {}).get("rates", {})) if stored_card else None
        repair_report = repairs.repair_options(catalog, key, card)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("repair evidence unavailable for %s: %s", key, exc)
    if supplied:
        limitations = [
            "The supplied archives contain no per-part timestamp, batch identifier or defect label.",
            "The image archive and the simulation exports share no key, so defect classes cannot be attached to "
            "stations from data.",
        ]
        if not cost_fields and not stored_card:
            limitations.insert(
                1,
                "The supplied archives contain no cost, scrap, rework or downtime values, so no economic figure "
                "can be computed for them.",
            )
    else:
        limitations = list(dict.fromkeys(
            [r for r in (capabilities.get("reasons") or {}).values()]
            + [l for l in (repair_report.get("limitations") or []) if l]
        ))
    return {
        "task": "overview",
        "dataset": {
            "name": record.get("name") or key,
            "dataset_id": record.get("id") or key,
            "key": key,
            "uploaded_at": record.get("uploaded_at"),
            "status": record.get("status"),
        },
        "repairs": {
            "statement": repair_report.get("statement"),
            "cheapest_supported_effective_repair": repair_report.get("cheapest_supported_effective_repair"),
            "candidates": [
                {
                    "repair": c.get("repair"),
                    "expected_loss_reduction": (c.get("expected_loss_reduction") or {}).get("amount"),
                    "intervention_cost": (c.get("intervention_cost") or {}).get("amount"),
                    "net_impact": (c.get("net_impact") or {}).get("amount"),
                    "confidence": c.get("confidence"),
                }
                for c in repair_report.get("candidates") or []
            ],
            "rate_card_supplied": bool(stored_card),
        },
        "models": (
            # Only the documented archives, and only for a supplied archive: an
            # uploaded CSV is not part of that facility and must not be described
            # as if it were, and a supplied archive must not inherit an upload's name.
            [
                m["label"]
                for m in catalog.dataset_summaries()
                if m["kind"] == "simulation" and m["present"] and m["key"] in domain.MODELS
            ]
            if supplied
            else [record.get("name") or key]
        ),
        "inspection": (
            {
                "classes": list(catalog.vision_classes.keys()),
                "counts": catalog.vision_classes,
                "localization_available": False,
            }
            if (capabilities.get("available") or {}).get("vision")
            else None
        ),
        "quality": {
            "usable_columns": columns,
            "missing_cells": sum(i.rows_affected for i in catalog.issues if i.message.startswith(f"{key}:") and "missing cells" in i.message),
            "duplicate_rows": sum(i.rows_affected for i in catalog.issues if i.message.startswith(f"{key}:") and "duplicated rows" in i.message),
            "anomalous_fraction": report["anomalous_fraction"],
            "issues": issues,
        },
        "bottleneck": {
            "label": top.get("label"),
            "utilization": top.get("utilization"),
            "capacity": top.get("capacity"),
            "queue_mean": top.get("queue_mean"),
            "headroom_pct": (top.get("headroom") or 0) * 100.0 if top else None,
            "ranking": [
                {"label": r["label"], "score": r["bottleneck_score"], "utilization": r["utilization"]}
                for r in bottleneck_ranking["ranking"][:4]
            ],
        },
        "calibration": analytics.calibration_report(catalog, key),
        #: The image-archive/simulation linkage is a property of the supplied
        #: archives; stating it for an uploaded CSV would describe data the user
        #: does not have.
        "linkage": catalog.linkage_report() if supplied else None,
        "capabilities": capabilities,
        "limitations": limitations,
        "unavailable": list(capabilities["reasons"].values()),
    }


def _case_evidence(case_id: str, key: str = "model3") -> Dict[str, Any]:
    case = forensics.build_case(catalog, key, case_id)
    return {
        "task": "case",
        "case_id": case["case_id"],
        "label": case["label"],
        "scope": case["scope"],
        "statistic": case["statistic"],
        "baseline_definition": case["baseline_definition"],
        "first_divergence": case["first_divergence"],
        "co_occurring": case["co_occurring"],
        "outcome": case["outcome"],
        "timeline": case["timeline"],
        "confidence": case["confidence"],
        "confidence_basis": case["confidence_basis"],
        "limitations": case["limitations"],
        "capabilities": catalog.capabilities(key),
    }


def _station_evidence(station: str, key: str = "model3") -> Dict[str, Any]:
    rows = bottleneck.station_rows(catalog, key)
    row = next((r for r in rows if r["station"] == station), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown station '{station}'")
    ranking = bottleneck.rank_bottlenecks(catalog, key)
    rank = next((r for r in ranking["ranking"] if r["station"] == station), None)
    return {
        "task": "station",
        "station": station,
        "label": row["label"],
        "stage": row["stage"],
        "capacity": row["capacity"],
        "capacity_source": row["capacity_source"],
        "utilization": row["utilization"],
        "utilization_source": row["utilization_source"],
        "queue_mean": row["queue_mean"],
        "queue_max": row["queue_max"],
        "headroom": row["headroom"],
        "rank": rank.get("rank") if rank else None,
        "score_components": (rank or {}).get("score_components", {}),
        "evidence": (rank or {}).get("evidence", []),
        "limitations": [
            "Utilisation ranks are computed from a stochastic simulation export, not from a physical line.",
            "No downtime, scrap or rework column exists, so a constraint cannot be attributed to a failure mode.",
        ],
        "unavailable": row.get("unavailable", []),
    }


@router.get("/ai/status")
def ai_status() -> Dict[str, Any]:
    return {**ai_client.status(), "usage": ai_client.usage_summary()}


@router.get("/ai/narrative")
def narrative(
    topic: str = Query("overview", pattern="^(overview|case|station|scenario)$"),
    subject_id: str | None = Query(None, description="case id or station key"),
    key: str = Query("", description="the dataset the narrative must be based on"),
    refresh: bool = Query(False, description="bypass the cache and regenerate"),
) -> Dict[str, Any]:
    if not key:
        raise HTTPException(
            status_code=400,
            detail="No dataset loaded. Please upload a dataset to begin.",
        )
    if key not in catalog.frames:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    # Evidence is addressed by the *raw* subject (case id, station key) because
    # that is what the analytics understand; the narrative cache key is qualified
    # with the dataset so one case's stored narrative can never be served for
    # another.
    raw_subject = subject_id or ("overview" if topic == "overview" else "")
    cache_subject = f"{key}:{raw_subject or topic}"
    if topic == "overview":
        evidence = _overview_evidence(key)
    elif topic == "case":
        evidence = _case_evidence(raw_subject or "group:lowest-output-1pct", key)
    elif topic == "station":
        if not raw_subject:
            raise HTTPException(status_code=400, detail="subject_id (station key) is required for the station topic")
        evidence = _station_evidence(raw_subject, key)
    else:
        evidence = {
            "task": "scenario",
            "engine": "discrete_event_resimulation",
            "summary": {"note": "no scenario was supplied; open the What-If view to generate one"},
            "validation": {},
            "limitations": ["A scenario narrative requires a scenario run."],
        }
    return ai_client.narrative(topic, cache_subject, evidence, use_cache=not refresh)


@router.post("/ai/ask")
def ask(payload: AIQuestion) -> Dict[str, Any]:
    """Answer a question strictly from backend-computed evidence.

    The dataset is required and is echoed back inside the subject id, so asking the
    same question on two datasets cannot return the other dataset's narrative.
    """
    if not payload.key:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please upload a dataset to begin.")
    if payload.key not in catalog.frames:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{payload.key}'")
    if payload.scope == "case":
        evidence = _case_evidence(payload.subject_id or "group:lowest-output-1pct", payload.key)
    elif payload.scope == "station":
        if not payload.subject_id:
            raise HTTPException(status_code=400, detail="subject_id (station key) is required")
        evidence = _station_evidence(payload.subject_id, payload.key)
    else:
        evidence = _overview_evidence(payload.key)
    evidence["question_scope"] = payload.scope
    return ai_client.narrative(
        "question",
        f"{payload.key}:{payload.subject_id or 'overview'}",
        evidence,
        question=payload.question,
    )


@router.get("/ai/logs")
def ai_logs(limit: int = Query(25, ge=1, le=200)) -> Dict[str, Any]:
    """Every AI API attempt, so usage and failures stay visible."""
    return {"logs": ai_client.recent_logs(limit=limit), "usage": ai_client.usage_summary()}
