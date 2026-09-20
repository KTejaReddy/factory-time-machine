"""Payload shapes the UI and the live smoke test depend on.

Each of these was found broken *live* while verifying the app (a 500, a blank
page, or a card that could never appear), so they are pinned here rather than in
the module that happens to compute them.
"""

from __future__ import annotations

import json

import numpy as np

from app.services import analytics, forensics, vision


def test_numpy_values_render_as_json(catalog):
    """The analytics compute in numpy; the default encoder refused its scalars.

    ``/api/production/demand-response`` for Models 1 and 2 returned numpy demand
    levels and answered 500 despite computing correctly.
    """
    from app.json_utils import NumpySafeJSONResponse, to_native

    response = NumpySafeJSONResponse(
        content={"levels": [np.int64(1), np.int64(2)], "value": np.float32(0.5), "array": np.array([1.0, 2.0])}
    )
    assert json.loads(response.body) == {"levels": [1, 2], "value": 0.5, "array": [1.0, 2.0]}
    assert to_native({"a": np.int32(3), "b": [np.bool_(True)]}) == {"a": 3, "b": [True]}


def test_nan_is_converted_to_null_not_invalid_json():
    from app.json_utils import finite_or_none

    assert finite_or_none(float("nan")) is None
    assert finite_or_none(float("inf")) is None
    assert finite_or_none(2.5) == 2.5


def test_demand_response_is_serialisable_for_every_model(catalog):
    for key in ("model1", "model2", "model3"):
        report = analytics.demand_response(catalog, key)
        json.dumps(report)  # raises if a numpy scalar survived
        if report.get("status") != "unavailable":
            assert all(type(level) is int for level in report["factor_levels"])


def test_catalog_status_exposes_progress_and_stages(catalog):
    snapshot = catalog.status.snapshot()
    assert snapshot["state"] == "ready"
    assert isinstance(snapshot["progress"], float)
    assert isinstance(snapshot["stages"], dict)


def test_column_profiles_flag_documented_versus_undocumented(catalog):
    """Every column used to claim ``documented=True``, which made the UI filter a no-op."""
    profiles = catalog.profiles["model3"]
    documented = [p for p in profiles if p.documented]
    undocumented = [p for p in profiles if not p.documented]
    assert documented and undocumented, "both sides must exist in the Model 3 export"
    for other in undocumented:
        assert other.note, f"{other.name} is undocumented with no explanation"


def test_forensic_case_confidence_basis_is_a_list(catalog):
    case_id = forensics.list_cases(catalog, "model3")[0]["case_id"]
    case = forensics.build_case(catalog, "model3", case_id)
    assert isinstance(case["confidence_basis"], list) and case["confidence_basis"]
    assert isinstance(case["evidence"], list)
    assert case["causal_caveat"]


def test_vision_metrics_declare_localization_availability(catalog):
    payload = vision.vision_metrics_payload(catalog)
    assert payload["localization"]["available"] is False
    assert "bounding boxes" in payload["localization"]["reason"]


def test_simulation_options_expose_a_per_change_map(catalog):
    from app.services import simulation

    options = simulation.scenario_options(catalog)
    offered = options["stations"]
    assert offered, "no adjustable station is offered"
    for station in offered:
        assert station["adjustable"]["capacity"] is True
        assert "processing_time" in station["adjustable"]
    assert isinstance(options["not_adjustable"], list) and options["not_adjustable"]


def test_anomaly_report_carries_the_documented_driver_fields(catalog):
    """The landing page crashed on ``driver.feature`` when only ``column`` was sent."""
    report = analytics.anomaly_report(catalog, "model3", top=3)
    assert report["status"] == "success"
    assert report["anomalous_runs"] > 0
    driver = report["top"][0]["drivers"][0]
    for field in ("feature", "direction", "z", "value", "baseline_mean"):
        assert field in driver, f"driver is missing {field}"
    assert driver["direction"] in {"high", "low"}
    importance = report["feature_importance"][0]
    assert "feature" in importance and "loading" in importance
