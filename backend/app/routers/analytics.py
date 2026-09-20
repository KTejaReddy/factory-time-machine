"""Production, bottleneck, forensic, propagation and economics endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from ..schemas import CostRateCard
from ..services import analytics, bottleneck, economics, forensics
from ..services.catalog import catalog

router = APIRouter(prefix="/api", tags=["analytics"])

def _check(key: str) -> str:
    if not key:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please upload a dataset to begin.")
    if key not in catalog.frames:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    return key


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------
@router.get("/production/snapshot")
def production_snapshot(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    try:
        return bottleneck.production_snapshot(catalog, key)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/production/bottlenecks")
def bottlenecks(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    ranking = bottleneck.rank_bottlenecks(catalog, key)
    return {**ranking, "headroom": bottleneck.capacity_headroom(catalog, key)}


@router.get("/production/anomalies")
def anomalies(key: str = Query(""), top: int = Query(25, ge=1, le=200)) -> Dict[str, Any]:
    _check(key)
    return analytics.anomaly_report(catalog, key, top=top)


@router.get("/production/regimes")
def regimes(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return analytics.regime_contrast(catalog, key)


@router.get("/production/demand-response")
def demand_response(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return analytics.demand_response(catalog, key)


@router.get("/production/calibration")
def calibration(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return analytics.calibration_report(catalog, key)


@router.get("/production/association")
def association(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return analytics.stage_association(catalog, key)


# ---------------------------------------------------------------------------
# Forensics
# ---------------------------------------------------------------------------
@router.get("/diagnostics/cases")
def cases(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return {"model_key": key, "cases": forensics.list_cases(catalog, key)}


@router.get("/diagnostics/case")
def case(
    case_id: str = Query(..., description="e.g. group:lowest-output-1pct or run:1234"),
    key: str = Query(""),
) -> Dict[str, Any]:
    _check(key)
    try:
        return forensics.build_case(catalog, key, case_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/diagnostics/propagation")
def propagation(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    from ..db import LinkageAssumption, list_rows

    assumptions = [row.as_dict() for row in list_rows(LinkageAssumption, limit=50)]
    try:
        return forensics.propagation_graph(catalog, key, assumptions)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/diagnostics/stations")
def stations(key: str = Query("")) -> Dict[str, Any]:
    """Per-station detail used by the propagation graph node panel."""
    _check(key)
    rows = bottleneck.station_rows(catalog, key)
    baseline = forensics.station_baseline(catalog, key)
    return {
        "model_key": key,
        "stations": [
            {
                **row,
                "baseline": baseline["stations"].get(row["station"], {}).get("metrics", {}),
                "severity": forensics.severity_from_z(
                    _station_z(row, baseline["stations"].get(row["station"], {}))
                ),
            }
            for row in rows
        ],
    }


def _station_z(row: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[float]:
    util = row.get("utilization")
    stats = (baseline.get("metrics") or {}).get("utilization")
    if util is None or not stats or not stats.get("std"):
        return None
    return (util - stats["mean"]) / stats["std"]


# ---------------------------------------------------------------------------
# Economics
# ---------------------------------------------------------------------------
@router.get("/economics/assessment")
def economics_assessment(key: str = Query("")) -> Dict[str, Any]:
    _check(key)
    return economics.assess(catalog, key)


@router.post("/economics/assessment")
def economics_assessment_with_rates(
    key: str = Query(""),
    rates: CostRateCard = Body(CostRateCard()),
    save: bool = Query(True, description="store the rate card with this dataset"),
) -> Dict[str, Any]:
    """Compute an advisory figure from measured quantities and user-supplied rates.

    The rate card is stored **against this dataset**, which is what makes the
    economics dataset-specific: another dataset never silently inherits these
    rates, and reopening this dataset reuses them.
    """
    _check(key)
    result = economics.assess(catalog, key, rates)
    if save:
        from ..services import workspace

        try:
            workspace.save_rate_card(key, rates.model_dump())
            result["rate_card_saved"] = True
        except Exception as exc:  # pragma: no cover - storage is best effort
            result["rate_card_saved"] = False
            result["rate_card_save_error"] = str(exc)
    return result


@router.get("/economics/requirements")
def economics_requirements() -> Dict[str, Any]:
    return {
        "missing_variables": economics.MISSING_VARIABLES,
        "arena_costing_not_exported": economics.ARENA_COSTING_NOT_EXPORTED,
        "disclaimer": economics.DISCLAIMER,
        "explanation": (
            "The Arena models configure costing constructs, but no cost value is exported in any CSV. A rate "
            "card is required before any monetary figure can be shown, and every line records whether the "
            "quantity came from the dataset and whether the rate came from the user."
        ),
    }
