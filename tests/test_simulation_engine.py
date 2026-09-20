"""Discrete-event engine correctness.

The utilisation calibration check passed while the queue statistics were broken,
because total service time is conserved regardless of *when* parts are admitted.
That asymmetry is why the queue side needs its own explicit guards.
"""

from __future__ import annotations

import random

import pytest

from app.config import settings
from app.services import simulation


@pytest.fixture(scope="module")
def baseline_metrics(catalog):
    return simulation.baseline_metrics(catalog)


@pytest.fixture(scope="module")
def baseline_run(catalog, baseline_metrics):
    """One full replication of the documented route, with no overrides."""
    run = simulation.DesRun(
        horizon=settings.sim_horizon_seconds,
        n_parts=int(round(baseline_metrics["assembled_parts_mean"])),
        capacity_overrides={},
        time_factors={},
        rng=random.Random(settings.sim_random_seed),
        sku_share=None,
        qc_batch_size=baseline_metrics.get("qc_batch_parts") or 87.0,
        paint_delay_seconds=baseline_metrics.get("paint_delay_seconds")
        or simulation.DesRun.DEFAULT_PAINT_DELAY_SECONDS,
        routing_share=baseline_metrics.get("routing_share"),
    )
    return run.run()


# ---------------------------------------------------------------------------
# The bug this guards: all arrivals were admitted in a pre-pass, so every queue
# was time-weighted from a clock already at the end of the horizon. Queue maxima
# equalled the whole production volume and time-weighted means went negative.
# ---------------------------------------------------------------------------
def test_no_queue_ever_goes_backwards_in_time(baseline_metrics):
    violations: list[tuple[float, float]] = []
    original = simulation.QueueStat.set_time

    def traced(self, t: float) -> None:  # noqa: ANN001
        if t < self._last - 1e-9:
            violations.append((self._last, t))
        original(self, t)

    simulation.QueueStat.set_time = traced  # type: ignore[method-assign]
    try:
        simulation.DesRun(
            horizon=settings.sim_horizon_seconds,
            n_parts=6_000,
            capacity_overrides={},
            time_factors={},
            rng=random.Random(1),
            sku_share=None,
            qc_batch_size=87.0,
            paint_delay_seconds=simulation.DesRun.DEFAULT_PAINT_DELAY_SECONDS,
            routing_share=baseline_metrics.get("routing_share"),
        ).run()
    finally:
        simulation.QueueStat.set_time = original  # type: ignore[method-assign]

    assert not violations, (
        f"{len(violations)} queue clock step(s) went backwards, e.g. "
        f"{violations[:3]} - a queue was advanced past the current event time"
    )


def test_queue_means_are_never_negative(baseline_run):
    for key, station in baseline_run["stations"].items():
        assert station["queue_mean"] >= 0, f"{key} reported a negative mean queue length"


def test_queue_maxima_are_physically_possible(baseline_run):
    """A queue can never exceed the number of parts released into the system."""
    released = baseline_run["released_parts"]
    for key, station in baseline_run["stations"].items():
        assert station["queue_max"] <= released, (
            f"{key} reported a queue of {station['queue_max']} against only {released} parts released"
        )


def test_queue_max_is_at_least_the_mean(baseline_run):
    for key, station in baseline_run["stations"].items():
        assert station["queue_max"] >= station["queue_mean"] - 1e-9, (
            f"{key}: max queue {station['queue_max']} is below mean {station['queue_mean']}"
        )


def test_parts_are_admitted_across_the_horizon_not_all_at_once(baseline_run):
    """If arrivals were front-loaded, the busiest queue would hold a large slice
    of the whole production volume. Presses see roughly a quarter of the parts
    each, so a healthy run leaves them with queues of a few units."""
    for key in ("press1", "press2", "press3", "press4"):
        station = baseline_run["stations"][key]
        assert station["queue_max"] < baseline_run["released_parts"] * 0.01, (
            f"{key} queued {station['queue_max']} parts - arrivals look front-loaded again"
        )


def test_utilisation_is_reproduced_for_the_stations_that_validate(catalog):
    """The re-simulation must keep reproducing the export on the stations whose
    documented capacity agrees with the data. Residuals are shown to the user, so
    they are asserted here too."""
    sim = simulation._simulate(catalog, simulation.ScenarioSpec(name="t", kind="combined"))
    checks = {row["station"]: row for row in simulation.validate(catalog, sim)["rows"]}

    for station in ("press1", "press2", "press3", "press4", "cell1", "cell2", "cell3"):
        assert station in checks, f"{station} is missing from the validation payload"
        residual = abs(checks[station]["residual_pct"])
        assert residual < 5.0, f"{station} residual {residual:.2f}% exceeds 5%"

    # Cell 4 is *expected* to disagree: the export's utilisation implies a capacity
    # of 5 while Model 3.pdf documents 4. That conflict is a finding, not a
    # regression, so it is asserted to remain visible rather than tuned away.
    assert "cell4" in checks, "the Cell 4 conflict must stay visible in the validation payload"
    assert abs(checks["cell4"]["residual_pct"]) > 5.0, (
        "Cell 4 now agrees with the export - re-check whether the documented capacity changed"
    )


def test_capacity_override_moves_only_the_named_station(catalog):
    """A capacity change must be local: other stations keep their baseline utilisation."""
    base = simulation._simulate(catalog, simulation.ScenarioSpec(name="base", kind="combined"))
    bumped = simulation._simulate(
        catalog,
        simulation.ScenarioSpec(name="more press capacity", kind="capacity_change", station="press1", capacity_delta=2),
    )

    assert bumped["capacity_overrides"] == {"press1": int(bumped["stations"]["press1"]["capacity"])}
    assert bumped["stations"]["press2"]["utilization"] == pytest.approx(
        base["stations"]["press2"]["utilization"], rel=0.05
    )
    # More capacity at the press must not make the press busier per server.
    assert bumped["stations"]["press1"]["utilization"] < base["stations"]["press1"]["utilization"]


# ---------------------------------------------------------------------------
# The 'current' column must never mix two different measurements
# ---------------------------------------------------------------------------
def test_queue_metrics_are_compared_engine_to_engine(catalog):
    """The exported Queue columns are not a time-weighted mean waiting length.

    Comparing them against a simulated mean produced deltas near -100% that were a
    definition mismatch, not a scenario effect. Queue rows must therefore use this
    engine's own unmodified baseline as 'current'.
    """
    result = simulation.run_scenario(
        catalog,
        simulation.ScenarioSpec(name="t", kind="capacity_change", station="cell4", capacity_delta=1, replications=1),
    )
    for row in result["comparisons"]:
        if row["metric"] in ("queue_mean", "queue_max"):
            assert row["comparison_basis"] == "re-simulation baseline", (
                f"{row['station']} {row['metric']} was compared against {row['comparison_basis']!r}"
            )
            assert row["comparison_basis_note"], "a non-like-for-like row must explain itself"
            # The export value is still carried, but only as context.
            assert "export_reference_value" in row
        else:
            assert row["comparison_basis"] == "export (validated)", (
                f"{row['station']} {row['metric']} should be validated against the export"
            )


def test_utilisation_rows_are_compared_against_the_export(catalog):
    result = simulation.run_scenario(
        catalog, simulation.ScenarioSpec(name="t", kind="capacity_change", station="press1", capacity_delta=1, replications=1)
    )
    util = [r for r in result["comparisons"] if r["metric"] == "utilization"]
    assert util, "no utilisation comparisons were produced"
    for row in util:
        assert row["comparison_basis"] == "export (validated)"


def test_processing_time_change_slows_the_targeted_stage(catalog):
    base = simulation._simulate(catalog, simulation.ScenarioSpec(name="base", kind="combined"))
    slower = simulation._simulate(
        catalog,
        simulation.ScenarioSpec(
            name="slower cells", kind="processing_time_change", station="cell1", time_factor=1.5, replications=1
        ),
    )
    assert slower["time_factors"] == {"cell": 1.5}
    assert slower["stations"]["cell1"]["utilization"] > base["stations"]["cell1"]["utilization"]
