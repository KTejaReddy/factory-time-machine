"""Multi-dataset isolation: one case file per dataset, and nothing leaks between them.

The behaviour these tests pin is the product rule "never mix the cases":

* every loaded dataset has one registry record with a stable id;
* two uploaded datasets keep separate analysis, rate cards, scenarios, feedback
  and reports;
* a repair is only ranked economically when both its effect and its intervention
  cost are supported - otherwise the engine says so in one sentence;
* deleting a dataset's source file keeps its history.

The tests write their CSVs into the *patched* upload directory and clean up every
row they create, so the real workspace is left exactly as they found it.
"""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import AnalysisRun, DatasetRecord, FeedbackItem, RateCardEntry, ScenarioRun, session
from app.main import app
from app.schemas import CostRateCard
from app.services import analysis as analysis_service
from app.services import catalog as catalog_module
from app.services import forensics, repairs, reports, workspace
from app.services.catalog import catalog
from app.routers import upload as upload_router

#: Station profiles: dataset A's constraint is Assembly, dataset B's is Press 2.
_A_STATIONS = {
    "Cutting": (41.5, 0.71, 5.2, 0.51, 3, 2),
    "Welding": (52.0, 0.85, 18.0, 1.02, 5, 4),
    "Assembly": (64.0, 0.95, 31.0, 1.31, 9, 7),
}
_B_STATIONS = {
    "Press 1": (12.0, 55.0, 12.0, 14.0, 6, 3),
    "Press 2": (17.5, 91.0, 48.0, 39.0, 19, 11),
    "Press 3": (9.4, 48.0, 5.0, 8.0, 2, 1),
}
SHIFTS = 150


def _csv_a() -> str:
    lines = [
        "batch_id,station,cycle_time_s,station_util,queue_len,downtime_hours,scrap_count,rework_count,"
        "scrap_cost,downtime_cost,unit_value"
    ]
    for i in range(1, SHIFTS + 1):
        wobble = 1.0 + ((i % 7) - 3) / 100.0
        for station, (cycle, util, queue, down, scrap, rework) in _A_STATIONS.items():
            lines.append(
                f"B{i:04d},{station},{cycle * wobble:.2f},{min(0.999, util * wobble):.4f},"
                f"{queue * wobble:.2f},{down * wobble:.3f},{scrap},{rework},118.0,340.0,96.0"
            )
    return "\n".join(lines) + "\n"


def _csv_b() -> str:
    lines = ["shift_id,machine,Cycle_Time_min,util_pct,queue_parts,downtime_min,defects,rework_units"]
    for i in range(1, SHIFTS + 1):
        wobble = 1.0 + ((i % 5) - 2) / 100.0
        for machine, (cycle, util, queue, down, defects, rework) in _B_STATIONS.items():
            lines.append(
                f"S{i:04d},{machine},{cycle * wobble:.3f},{min(99.9, util * wobble):.2f},"
                f"{queue * wobble:.1f},{down * wobble:.1f},{defects},{rework}"
            )
    return "\n".join(lines) + "\n"


DATASET_A = _csv_a()
DATASET_B = _csv_b()
A_ROWS = SHIFTS * len(_A_STATIONS)

A_KEY = "user:test_isolation_a"
B_KEY = "user:test_isolation_b"
TEMP_KEYS = (A_KEY, B_KEY)

RATES = CostRateCard(
    currency="INR",
    cost_per_unit_scrapped=850.0,
    rework_cost_per_unit=420.0,
    downtime_cost_per_hour=12_000.0,
    holding_cost_per_unit_hour=40.0,
    margin_per_unit=260.0,
    intervention_cost_fixed=150_000.0,
    intervention_cost_per_capacity_unit=450_000.0,
)


@pytest.fixture()
def two_datasets(tmp_path, monkeypatch, catalog):
    """Two uploaded datasets in an isolated upload directory, cleaned up afterwards."""
    monkeypatch.setattr(upload_router, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(catalog_module, "USER_UPLOAD_DIR", tmp_path)
    (tmp_path / "test_isolation_a.csv").write_text(DATASET_A, encoding="utf-8")
    (tmp_path / "test_isolation_b.csv").write_text(DATASET_B, encoding="utf-8")
    catalog.build()
    assert A_KEY in catalog.frames and B_KEY in catalog.frames
    try:
        yield {"a": A_KEY, "b": B_KEY}
    finally:
        # Remove every row these tests created, so the real workspace keeps the
        # exact history it had before the suite ran.
        with session() as db:
            for model in (AnalysisRun, FeedbackItem, RateCardEntry, ScenarioRun):
                db.execute(delete(model).where(model.dataset_key.in_(TEMP_KEYS)))
            db.execute(delete(DatasetRecord).where(DatasetRecord.key.in_(TEMP_KEYS)))
        monkeypatch.undo()
        catalog.build()
        assert A_KEY not in catalog.frames


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_every_loaded_dataset_has_one_case_file(catalog):
    records = {r["key"]: r for r in workspace.list_records()}
    for key in ("model1", "model2", "model3", "images"):
        assert key in records, f"{key} has no case file"
    assert records["model3"]["id"] == "ds-model3"
    assert records["images"]["status"] == "images_only", "the image archive must not claim production analysis"
    assert records["model3"]["capabilities"]["available"]["production"] is True

    # Idempotent: a second build must not create a second record for the same key.
    catalog.build()
    again = [r["key"] for r in workspace.list_records() if r["key"] == "model3"]
    assert len(again) == 1


def test_uploaded_datasets_are_separate_case_files(two_datasets):
    records = {r["key"]: r for r in workspace.list_records()}
    a, b = records[A_KEY], records[B_KEY]
    assert a["id"] == "ds-test-isolation-a" and b["id"] == "ds-test-isolation-b"
    assert a["id"] != b["id"]
    assert a["rows"] == A_ROWS and b["rows"] == A_ROWS
    assert a["present"] and b["present"]
    # A carries cost columns, B does not: the capability maps must differ.
    assert a["capabilities"]["available"]["economics"] is True
    assert b["capabilities"]["available"]["economics"] is False
    assert b["capabilities"]["reasons"]["economics"]


def test_forensic_cases_come_from_the_dataset_itself(two_datasets, catalog):
    """The Model 3 output series must never be used to select another dataset's rows."""
    cases_a = forensics.list_cases(catalog, A_KEY)
    assert cases_a, "a station-per-row dataset must offer its own cases"
    assert all("station:" in c["case_id"] for c in cases_a)
    labels = {c["label"] for c in cases_a}
    assert "Behaviour of Assembly" in labels
    assert all("Lowest-output" not in label for label in labels)

    built = forensics.build_case(catalog, A_KEY, cases_a[0]["case_id"])
    assert built["selection"]["n"] > 0
    assert built["timeline"], "the dataset's own process columns must produce a divergence timeline"
    assert all(e["metric"] in DATASET_A.splitlines()[0] for e in built["timeline"])


def test_analysis_results_stay_with_their_dataset(two_datasets, catalog):
    run_a = analysis_service.run_analysis(catalog, A_KEY, rate_card=None, include_ai=False)
    run_b = analysis_service.run_analysis(catalog, B_KEY, rate_card=None, include_ai=False)

    assert run_a["dataset"]["key"] == A_KEY and run_b["dataset"]["key"] == B_KEY
    assert run_a["dataset"]["dataset_id"] == "ds-test-isolation-a"

    bottleneck_a = run_a["sections"]["production"]["bottleneck"]["label"]
    bottleneck_b = run_b["sections"]["production"]["bottleneck"]["label"]
    assert bottleneck_a == "Assembly" and bottleneck_b == "Press 2"
    assert bottleneck_a != bottleneck_b

    # Stored separately and retrieved by key.
    assert workspace.latest_analysis(A_KEY)["payload"]["dataset"]["key"] == A_KEY
    assert workspace.latest_analysis(B_KEY)["payload"]["dataset"]["key"] == B_KEY
    assert workspace.latest_analysis(A_KEY)["id"] != workspace.latest_analysis(B_KEY)["id"]


def test_scenario_history_is_filtered_by_dataset(two_datasets):
    client = TestClient(app)
    for key, name in ((A_KEY, "A scenario"), (B_KEY, "B scenario")):
        scenario = {"model_key": "model3", "name": name, "kind": "combined"}
        response = client.post(
            f"/api/simulation/run?key={key}&save=true",
            json={"spec": scenario, "rates": None},
        )
        # Neither dataset is covered by the documented-route engine, so the honest
        # answer is a refusal - and nothing may be stored for the wrong dataset.
        assert response.status_code == 400, response.text
        assert "Model 3" in response.json()["detail"]

    # A scenario stored for A must not appear in B's history.
    from app.db import insert_row

    row = insert_row(ScenarioRun(dataset_key=A_KEY, name="stored for A", engine="des"))
    try:
        history_a = client.get(f"/api/simulation/history?key={A_KEY}").json()
        history_b = client.get(f"/api/simulation/history?key={B_KEY}").json()
        assert any(r["id"] == row.id for r in history_a)
        assert all(r["id"] != row.id for r in history_b)
    finally:
        with session() as db:
            db.execute(delete(ScenarioRun).where(ScenarioRun.id == row.id))


def test_feedback_and_rate_cards_belong_to_one_dataset(two_datasets):
    client = TestClient(app)

    feedback = client.post(
        "/api/review/feedback",
        json={
            "dataset_key": A_KEY,
            "finding_id": "case:group:station:Assembly",
            "finding_kind": "forensic_case",
            "finding_title": "Assembly diverges",
            "decision": "confirmed",
            "note": "operator confirmed",
        },
    )
    assert feedback.status_code == 200
    assert feedback.json()["item"]["dataset_key"] == A_KEY

    assert client.get(f"/api/review/summary?key={A_KEY}").json()["total"] == 1
    assert client.get(f"/api/review/summary?key={B_KEY}").json()["total"] == 0

    saved = client.post(f"/api/workspace/rate-card?key={A_KEY}", json=RATES.model_dump())
    assert saved.status_code == 200
    assert client.get(f"/api/workspace/rate-card?key={A_KEY}").json()["rate_card"]["cost_per_unit_scrapped"] == 850.0
    assert client.get(f"/api/workspace/rate-card?key={B_KEY}").json()["rate_card"] is None


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------
def test_repairs_never_invent_a_price(two_datasets, catalog):
    report = repairs.repair_options(catalog, B_KEY, None)
    assert report["status"] == "cost_comparison_unavailable"
    assert report["statement"] == "Repair cost comparison unavailable until a rate card is provided."
    assert report["cheapest_supported_effective_repair"] is None
    for candidate in report["candidates"]:
        assert candidate["intervention_cost"]["amount"] is None
        assert candidate["net_impact"]["amount"] is None
    # No rate is available at all for this dataset: it has no cost column.
    assert "scrap" not in report["rates"]


def test_dataset_cost_columns_supply_the_rate_without_a_card(two_datasets, catalog):
    report = repairs.repair_options(catalog, A_KEY, None)
    assert report["rates"], "dataset A carries cost columns, so some rates must be detected"
    assert "dataset column" in report["rates"]["scrap"]["source"]
    assert "dataset column" in report["rates"]["downtime"]["source"]
    downtime = next((c for c in report["candidates"] if c["kind"] == "downtime"), None)
    assert downtime is not None
    assert downtime["expected_loss_reduction"]["amount"] is not None, "a measured quantity with a dataset rate must be valued"
    # ...but the intervention cost is never in the data, so no net impact yet.
    assert downtime["intervention_cost"]["amount"] is None
    assert downtime["net_impact"]["amount"] is None


def test_cheapest_supported_effective_repair_needs_both_sides(two_datasets, catalog):
    unpriced = repairs.repair_options(catalog, A_KEY, None)
    assert unpriced["cheapest_supported_effective_repair"] is None

    priced = repairs.repair_options(catalog, A_KEY, RATES)
    best = priced["cheapest_supported_effective_repair"]
    assert priced["status"] == "available" and best is not None
    assert best["net_impact"]["amount"] > 0
    assert best["intervention_cost"]["amount"] is not None
    assert best["benefit_cost_ratio"] >= 1
    assert best["confidence"] in {"high", "medium"}
    assert any("per-row" in line["unit"] or "row" in line["unit"] for line in best["expected_loss_reduction"]["lines"])
    # A repair that does not pay back is reported, but never selected.
    losers = [c for c in priced["candidates"] if (c["net_impact"]["amount"] or 0) <= 0]
    assert all(c["repair"] != best["repair"] for c in losers)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def test_reports_contain_only_their_own_dataset(two_datasets, catalog):
    analysis_service.run_analysis(catalog, A_KEY, rate_card=RATES, include_ai=False)
    analysis_service.run_analysis(catalog, B_KEY, rate_card=None, include_ai=False)

    report_a = reports.build_report(catalog, A_KEY, RATES)
    report_b = reports.build_report(catalog, B_KEY, None)
    assert report_a["dataset"]["id"] == "ds-test-isolation-a"
    assert report_b["dataset"]["id"] == "ds-test-isolation-b"

    markdown_a = reports.render_markdown(report_a)
    markdown_b = reports.render_markdown(report_b)
    assert "ds-test-isolation-a" in markdown_a and "ds-test-isolation-b" not in markdown_a
    assert "ds-test-isolation-b" in markdown_b and "ds-test-isolation-a" not in markdown_b
    assert "test_isolation_a" not in markdown_b
    assert "Dataset ID" in markdown_a and "Report generated" in markdown_a

    csv_a = reports.render_csv(report_a)
    assert "test_isolation_b" not in csv_a
    assert csv_a.startswith("dataset,dataset_id,generated_at,section,item,value,source")


def test_report_download_is_scoped_by_key(two_datasets, catalog):
    client = TestClient(app)
    response = client.get(f"/api/workspace/report?key={A_KEY}&format=csv&download=true")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "test_isolation_a" in response.text
    assert "test_isolation_b" not in response.text


def test_deleting_the_source_keeps_the_saved_history(two_datasets, catalog):
    analysis_service.run_analysis(catalog, A_KEY, rate_card=None, include_ai=False)
    path = workspace.uploaded_file_path(A_KEY)
    assert path is not None
    path.unlink()
    catalog.build()

    record = workspace.find_record(A_KEY)
    assert record is not None, "the case file must survive the file disappearing"
    assert record["present"] is False and record["status"] == "file_missing"
    assert A_KEY not in catalog.frames
    assert workspace.latest_analysis(A_KEY) is not None

    # And the API says why it cannot be analysed instead of raising a 500.
    client = TestClient(app)
    response = client.post(f"/api/workspace/analysis/run?key={A_KEY}")
    assert response.status_code == 409
    assert "source file is missing" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Presence flags
# ---------------------------------------------------------------------------
def test_a_wrongly_flagged_dataset_recovers_without_a_restart(two_datasets, catalog):
    """A build that raced an upload could flag a live dataset as deleted.

    The history stayed intact, but the switcher told the user their dataset was
    gone. Reads reconcile against the file on disk, so it recovers by itself.
    """
    with session() as db:
        row = db.execute(select(DatasetRecord).where(DatasetRecord.key == A_KEY)).scalar_one()
        row.present = False
        row.status = "file_missing"

    client = TestClient(app)
    record = next(r for r in client.get("/api/workspace/datasets").json()["datasets"] if r["key"] == A_KEY)
    assert record["present"] is True, "the CSV is still on disk, so the flag must self-heal"
    assert record["status"] != "file_missing"


def test_a_file_that_is_not_loaded_is_not_reported_as_deleted(two_datasets, catalog, monkeypatch):
    """Absent from the catalog is not the same as absent from the disk."""
    real = catalog

    class _BuildWithoutA:
        """A catalog snapshot that simply did not pick this CSV up."""

        def dataset_summaries(self):
            return [s for s in real.dataset_summaries() if s["key"] != A_KEY]

        def capabilities(self, key):
            return real.capabilities(key)

        def profile_map(self, key):
            return real.profile_map(key)

    try:
        workspace.sync_registry(_BuildWithoutA())
        record = workspace.find_record(A_KEY)
        assert record["present"] is True
        assert record["status"] != "file_missing", "an unloaded file is not a deleted file"
    finally:
        catalog.build()

    # A file on disk that no build has loaded yet is honestly labelled instead.
    class _NothingLoaded:
        frames: dict = {}

    workspace.reconcile_presence(_NothingLoaded())
    assert workspace.find_record(A_KEY)["status"] == "pending_build"
    catalog.build()
    workspace.reconcile_presence(catalog)
    assert workspace.find_record(A_KEY)["status"] != "pending_build"


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
def test_ai_evidence_never_quotes_another_dataset(two_datasets, catalog):
    """The evidence bundle is what the model may talk about, so it must be scoped.

    An uploaded dataset's narrative was quoting the supplied archive's missing
    cells and the simulation export's constant column - findings the user's own
    data never produced.
    """
    from app.routers.ops import _overview_evidence

    uploaded = json.dumps(_overview_evidence(A_KEY))
    for foreign in ("model1", "model2", "model3", "Time_Now", B_KEY.split(":")[1]):
        assert foreign not in uploaded, f"{foreign} leaked into an upload's evidence"
    assert _overview_evidence(A_KEY)["models"] == [workspace.find_record(A_KEY)["name"]]
    assert _overview_evidence(A_KEY)["linkage"] is None, "the image/simulation linkage is not this dataset's"

    supplied = json.dumps(_overview_evidence("model3"))
    assert A_KEY.split(":")[1] not in supplied, "an upload must not be described as part of the facility"


def test_ai_answers_are_grounded_on_the_active_dataset(two_datasets, catalog):
    client = TestClient(app)
    answers = {}
    for key in (A_KEY, B_KEY):
        response = client.post("/api/ai/ask", json={"question": "What is the main problem?", "scope": "overview", "key": key})
        assert response.status_code == 200, response.text
        body = response.json()
        answers[key] = body
        assert body["subject_id"].startswith(f"{key}:")
        assert body["grounded_on"]["dataset"]["key"] == key

    assert answers[A_KEY]["grounded_on"]["dataset"]["name"] != answers[B_KEY]["grounded_on"]["dataset"]["name"]
    assert answers[A_KEY]["subject_id"] != answers[B_KEY]["subject_id"]
    # The deterministic engine must describe the dataset it was given, not Model 3.
    assert "Assembly" in json.dumps(answers[A_KEY]) or "Assembly" in answers[A_KEY]["finding"]["finding"]
    assert "Model 3" not in answers[B_KEY]["finding"]["finding"] or "Press" in json.dumps(answers[B_KEY])


def test_ai_refuses_to_guess_a_dataset():
    client = TestClient(app)
    response = client.post("/api/ai/ask", json={"question": "What is the main problem?", "scope": "overview"})
    assert response.status_code == 400
    assert "No dataset" in response.json()["detail"]
