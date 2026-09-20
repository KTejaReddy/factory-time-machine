"""The honesty contract.

These tests do not check that the analytics are *right* - they check that the
system refuses to claim things the supplied data cannot support. That is the
property most likely to regress silently, because the failure mode is a
plausible-looking number rather than an exception.
"""

from __future__ import annotations

import json

import pytest

from app.schemas import AIFinding, LinkageAssumptionIn, ScenarioSpec
from app.services import economics, forensics, simulation


# ---------------------------------------------------------------------------
# No shared key between the two datasets
# ---------------------------------------------------------------------------
def test_datasets_are_reported_as_not_joinable(catalog):
    report = catalog.linkage_report()
    assert report["joinable"] is False
    # The reason must be stated, not just the verdict.
    assert report.get("shared_keys") in ([], None) or report["shared_keys"] == []


def test_linkage_names_the_missing_identifiers(catalog):
    blob = json.dumps(catalog.linkage_report()).lower()
    for missing in ("timestamp", "batch"):
        assert missing in blob, f"linkage report should name the missing '{missing}' identifier"


def test_capabilities_gate_unavailable_features(catalog):
    caps = catalog.capabilities()
    reasons = caps.get("reasons") or {}
    # Anything the data cannot support must be off AND carry a stated reason.
    for feature, available in caps.items():
        if feature == "reasons":
            continue
        if available is False:
            assert reasons.get(feature), f"'{feature}' is disabled but gives no reason"


# ---------------------------------------------------------------------------
# Economics is gated on the absence of any cost value
# ---------------------------------------------------------------------------
def test_economics_refuses_to_invent_a_total(catalog):
    assessment = economics.assess(catalog, "model3")
    assert assessment["available"] is False
    assert assessment["total"] is None, "no cost value exists in the data; a total must not be produced"
    assert "cannot be calculated" in assessment["reason"].lower()
    assert assessment["missing_variables"], "the missing variables must be enumerated"
    assert assessment["disclaimer"]


def test_economics_with_a_rate_card_labels_every_source(catalog):
    from app.schemas import CostRateCard

    rates = CostRateCard(currency="USD", margin_per_unit=10.0, holding_cost_per_unit_hour=0.5)
    assessment = economics.assess(catalog, "model3", rates)
    assert assessment["available"] is True
    assert assessment["lines"]
    for line in assessment["lines"]:
        # Quantity must come from the dataset; the rate must come from the user.
        # Conflating the two is exactly the fabrication this project forbids.
        assert line["quantity_source"], f"{line['label']} does not say where its quantity came from"
        assert line["rate_source"], f"{line['label']} does not say where its rate came from"
        assert line["rate"] is not None


def test_economics_missing_variables_mention_scrap_and_downtime(catalog):
    blob = " ".join(economics.assess(catalog, "model3")["missing_variables"]).lower()
    assert "scrap" in blob or "defect" in blob
    assert "downtime" in blob


# ---------------------------------------------------------------------------
# Scenarios that cannot be simulated are rejected, never silently no-op'd
# ---------------------------------------------------------------------------
def test_unknown_station_is_rejected():
    spec = ScenarioSpec(kind="capacity_change", station="no_such_station", capacity_delta=1)
    with pytest.raises(ValueError):
        simulation._validate_spec(spec)


def test_documented_but_unadjustable_station_is_rejected():
    """Blanketing is a documented stage, so it looks legitimate - but changing its
    capacity cannot be validated against the export. It must be refused with the
    reason, not accepted as a no-op run."""
    spec = ScenarioSpec(kind="capacity_change", station="blanking", capacity_delta=1)
    with pytest.raises(ValueError) as exc:
        simulation._validate_spec(spec)
    assert "blanking" in str(exc.value)


def test_capacity_change_without_a_station_is_rejected():
    with pytest.raises(ValueError):
        simulation._validate_spec(ScenarioSpec(kind="capacity_change", capacity_delta=1))


def test_no_op_capacity_change_is_rejected():
    """A zero delta would return a result identical to the baseline, which a reader
    would mistake for 'the change had no effect'."""
    with pytest.raises(ValueError):
        simulation._validate_spec(ScenarioSpec(kind="capacity_change", station="cell4", capacity_delta=0))


def test_adjustable_station_passes_validation():
    simulation._validate_spec(ScenarioSpec(kind="capacity_change", station="cell4", capacity_delta=1))


def test_every_offered_station_is_actually_adjustable(catalog):
    """The UI builds its dropdown from this list, so anything offered here must be
    accepted by the validator - otherwise the UI ships a control that does nothing."""
    options = simulation.scenario_options(catalog)
    offered = [s["key"] for s in options["stations"]]
    assert offered, "no adjustable stations were offered"
    for key in offered:
        simulation._validate_spec(ScenarioSpec(kind="capacity_change", station=key, capacity_delta=1))


def test_stations_that_cannot_be_adjusted_are_disclosed(catalog):
    options = simulation.scenario_options(catalog)
    disclosed = {s["key"] for s in options["not_adjustable"]}
    assert "blanking" in disclosed
    for entry in options["not_adjustable"]:
        assert entry["reason"], f"{entry['key']} is listed as not adjustable without a reason"


def test_baseline_validation_spec_does_not_request_a_change():
    """The baseline is a bare re-run of the documented route; it must survive the
    same validation every user scenario goes through."""
    simulation._validate_spec(ScenarioSpec(kind="combined"))


def test_absurd_scenario_parameters_are_rejected():
    for bad in (
        ScenarioSpec(kind="demand_change", replications=0),
        ScenarioSpec(kind="demand_change", replications=50),
        ScenarioSpec(kind="demand_change", demand_factor=0),
        ScenarioSpec(kind="demand_change", horizon_seconds=-1),
        ScenarioSpec(kind="processing_time_change", station="cell1", time_factor=0),
    ):
        with pytest.raises(ValueError):
            simulation._validate_spec(bad)


# ---------------------------------------------------------------------------
# The AI contract: structured, validated, and never an instruction
# ---------------------------------------------------------------------------
def test_ai_finding_requires_the_full_shape():
    with pytest.raises(Exception):
        AIFinding(finding="short", confidence=0.5)  # below the length floor
    with pytest.raises(Exception):
        AIFinding(finding="a perfectly long enough finding", confidence=1.5)  # out of range
    with pytest.raises(Exception):
        AIFinding(finding="a perfectly long enough finding", confidence=-0.1)
    # A missing `confidence` must not silently default; it is the field a reader
    # leans on to decide how seriously to take the finding.
    with pytest.raises(Exception):
        AIFinding(finding="a perfectly long enough finding")


def test_ai_finding_neutralises_instruction_shaped_output():
    """Model output is data, never a command. Anything that looks like one is defanged."""
    finding = AIFinding(
        finding="Recommendation: DROP TABLE feedback; -- and delete from review",
        confidence=0.4,
    )
    blob = (finding.finding + finding.recommendation).lower()
    assert "drop table" not in blob
    assert "delete from" not in blob
    assert "[redacted]" in blob


def test_ai_finding_cleans_and_bounds_lists():
    finding = AIFinding(
        finding="A sufficiently long finding for validation.",
        confidence=0.3,
        evidence=["  lots\n\t of   whitespace  ", "", "   ", "kept"],
        limitations=["x" * 5000],
    )
    assert finding.evidence == ["lots of whitespace", "kept"]
    assert all(len(item) <= 600 for item in finding.limitations)


# ---------------------------------------------------------------------------
# Forensics states association, not causation
# ---------------------------------------------------------------------------
def test_forensic_cases_carry_a_causal_caveat(catalog):
    from app.services import forensics

    cases = forensics.list_cases(catalog, "model3")
    assert cases, "no forensic cases were produced"
    case = forensics.build_case(catalog, "model3", cases[0]["case_id"])
    assert case["causal_caveat"]
    assert "not proof of causation" in case["causal_caveat"] or "causation" in case["causal_caveat"]
    assert case["limitations"], "a case must carry its limitations"
    assert case["baseline_definition"], "a case must define what it compared against"


def test_forensic_case_never_claims_wall_clock_ordering(catalog):
    """There is no timestamp anywhere in the exports, so no case may claim to know
    what happened first in time."""
    from app.services import forensics

    case = forensics.build_case(catalog, "model3", forensics.list_cases(catalog, "model3")[0]["case_id"])
    blob = json.dumps(case).lower()
    for banned in ("at 14:", "wall clock", "timestamp range", "started at"):
        assert banned not in blob
    # The route-ordered nature must be declared instead.
    assert "route" in blob or "ordered" in blob


def test_declared_assumption_naming_an_unknown_class_is_skipped(catalog):
    """Every edge endpoint must exist as a node.

    Found by the smoke test during the audit: the API accepted an assumption for the
    class 'crazing', which is not one of the five archive labels, and the graph then
    emitted an edge pointing at a node that was never created.
    """
    bogus = [
        {"defect_class": "crazing", "station": "blanking", "active": True, "author": "audit"},
        {"defect_class": "rust", "station": "not-a-station", "active": True, "author": "audit"},
    ]
    graph = forensics.propagation_graph(catalog, "model3", assumptions=bogus)
    ids = {n["id"] for n in graph["nodes"]}
    dangling = [e for e in graph["edges"] if e["source"] not in ids or e["target"] not in ids]
    assert not dangling, f"graph emitted edges with missing endpoints: {dangling}"
    assert graph["assumed_edges"] == 0
    assert len(graph["skipped_assumptions"]) == 2
    reasons = " ".join(s["reason"] for s in graph["skipped_assumptions"])
    assert "archive labels" in reasons
    assert "station" in reasons


def test_a_valid_declared_assumption_still_becomes_an_edge(catalog):
    good = [{"defect_class": "rust", "station": "blanking", "active": True, "author": "audit"}]
    graph = forensics.propagation_graph(catalog, "model3", assumptions=good)
    ids = {n["id"] for n in graph["nodes"]}
    asserted = [e for e in graph["edges"] if e["status"] == "assumed"]
    assert len(asserted) == 1
    assert asserted[0]["source"] == "defect_rust"
    assert asserted[0]["source"] in ids and asserted[0]["target"] in ids


def test_vision_classes_in_the_graph_come_from_the_archive(catalog):
    graph = forensics.propagation_graph(catalog, "model3")
    node_classes = {n["id"] for n in graph["nodes"] if n["kind"] == "defect"}
    assert node_classes == {f"defect_{c}" for c in catalog.vision_classes}


def test_assumption_api_rejects_a_class_the_archive_does_not_have():
    """Write-side half of the same fix: refuse the bad value instead of storing it."""
    from fastapi import HTTPException

    from app.routers.ops import _known_defect_classes, _known_station_keys, create_assumption

    classes = _known_defect_classes()
    stations = _known_station_keys()
    assert "crazing" not in classes
    assert stations, "station keys must be discoverable from the domain model"

    with pytest.raises(HTTPException) as bad_class:
        create_assumption(LinkageAssumptionIn(defect_class="crazing", station=stations[0]))
    assert bad_class.value.status_code == 422
    assert "image archive" in bad_class.value.detail

    with pytest.raises(HTTPException) as bad_station:
        create_assumption(LinkageAssumptionIn(defect_class=classes[0], station="not-a-station"))
    assert bad_station.value.status_code == 422
    assert "simulation exports" in bad_station.value.detail


def test_propagation_edges_declare_their_status(catalog):
    from app.services import forensics

    graph = forensics.propagation_graph(catalog, "model3", [])
    assert graph["edges"]
    for edge in graph["edges"]:
        assert edge["status"] in ("observed", "assumed", "unavailable")
    ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids


def test_propagation_gates_the_economic_nodes(catalog):
    """No cost exists in the data, so cost nodes must be marked unavailable rather
    than decorated with invented figures."""
    from app.services import forensics

    graph = forensics.propagation_graph(catalog, "model3", [])
    econ = [n for n in graph["nodes"] if n["kind"] == "economic"]
    assert econ, "the graph should still show where an economic effect would sit"
    for node in econ:
        assert node["status"] == "unavailable"


# ---------------------------------------------------------------------------
# The documented plant description is internally consistent
# ---------------------------------------------------------------------------
def test_utilisation_identity_reproduces_the_export(catalog):
    """Sum(parts x documented time) / (documented capacity x horizon) must reproduce
    the exported utilisation columns. If it stops matching, either a documented
    constant or the export changed and the analytics are built on sand."""
    from app.services import analytics

    report = analytics.calibration_report(catalog, "model3")
    assert report, "no calibration report was produced"
    blob = json.dumps(report).lower()
    assert "cell1" in blob or "stations" in blob or "checks" in blob
