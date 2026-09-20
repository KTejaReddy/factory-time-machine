"""What-if simulation endpoints."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException, Query

from ..db import ScenarioRun, insert_row, list_rows
from ..schemas import CostRateCard, ScenarioSpec
from ..services import economics, simulation
from ..services.catalog import catalog

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.get("/options")
def options(key: str = Query("")) -> Dict[str, Any]:
    """Only the scenarios the available data actually supports - for this dataset."""
    if not key:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please upload a dataset to begin.")
    if key not in catalog.frames:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{key}'")
    return simulation.scenario_options(catalog, key)


@router.post("/run")
def run(
    spec: ScenarioSpec = Body(...),
    rates: CostRateCard | None = Body(default=None),
    engineer: str = Query("anonymous"),
    key: str = Query("", description="the dataset this scenario belongs to"),
    save: bool = Query(True, description="store the run so it appears in the scenario history"),
) -> Dict[str, Any]:
    dataset_key = key or spec.model_key
    if dataset_key not in catalog.frames:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{dataset_key}'")
    card = rates
    if card is None:
        # A stored rate card belongs to this dataset and is used as-is; otherwise
        # the scenario economics stay unavailable rather than borrowing rates from
        # another dataset.
        from ..services import workspace as workspace_service

        stored = workspace_service.load_rate_card(dataset_key)
        if stored:
            try:
                card = CostRateCard(**(stored.get("rates") or {}))
            except Exception:  # pragma: no cover - defensive
                card = None
    try:
        result = simulation.run_scenario(catalog, spec, card, key=dataset_key)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if save:
        try:
            row = insert_row(
                ScenarioRun(
                    dataset_key=dataset_key,
                    name=spec.name,
                    engine=result["engine"],
                    scenario=json.dumps(spec.model_dump(), default=str),
                    baseline=json.dumps(result.get("current_snapshot", {}), default=str),
                    result=json.dumps(
                        {"comparisons": result["comparisons"], "summary": result["summary"]}, default=str
                    ),
                    validation=json.dumps(result.get("validation", {}), default=str),
                    engineer=engineer,
                )
            )
            result["saved_run_id"] = row.id
        except Exception as exc:  # pragma: no cover - storage is best effort
            result["saved_run_id"] = None
            result["save_error"] = str(exc)
    return result


@router.get("/history")
def history(
    key: str = Query("", description="only this dataset's saved scenarios"),
    limit: int = Query(25, ge=1, le=200),
) -> List[Dict[str, Any]]:
    rows = [row.as_dict() for row in list_rows(ScenarioRun, limit=limit)]
    if key:
        rows = [r for r in rows if r["dataset_key"] == key]
    return rows


@router.get("/validation")
def validation(key: str = Query("model3")) -> Dict[str, Any]:
    """Baseline validation of the re-simulation against the exported columns.

    The spec is a bare re-run of the documented route: ``combined`` changes nothing
    (zero capacity delta, unit time factor), so this is the untouched baseline the
    exported columns are compared against. Only the documented Model 3 route can be
    validated this way, and asking for another dataset says so instead of answering
    with Model 3's residual.
    """
    if key != "model3":
        raise HTTPException(
            status_code=409,
            detail=(
                "baseline validation exists for the documented Model 3 route only; this dataset has no "
                "documented route to validate against"
            ),
        )
    try:
        sim = simulation._simulate(catalog, ScenarioSpec(name="baseline validation", kind="combined"))
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "engine": "discrete_event_resimulation",
        "validation": simulation.validate(catalog, sim, key=key),
        "baseline": simulation.baseline_metrics(catalog, key=key),
        "advisory": simulation.ADVISORY,
    }
