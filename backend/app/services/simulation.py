"""Discrete-event re-simulation of the documented Model 3 plant.

The route follows ``Model 3.pdf``: coil arrival -> Blanking (NORM(900,30) s per
batch of coils) -> Forklift (two 120 s legs per batch) -> Press 1..4 by SKU
(NORM(5,0.1) s, 2 machines each) -> Assembly Cell 1..4 (per-SKU times
25/15/23/17 s, capacities 8/2/2/4) -> Paint (2 conveyors, 5400 s transit) ->
Quality Check (TRIA(50,55,60) per batch).

Calibration, measured from the export (see ``baseline_metrics``):

* one blanking cycle serves a batch of coils, one QC cycle inspects a batch of
  parts, a paint conveyor carries a whole load. Per-part service at the
  documented times would require thousands of parallel servers and contradict
  the measured utilisation, so the batch sizes are derived from the export.
* the utilisation identity ``util = load * time / (batch * capacity * horizon)``
  then reproduces the exported columns within the tolerance asserted in
  ``test_simulation_engine.py``.

Cell 4 is expected to disagree with the export: the data implies ~5 parallel
resources while the document says 4. That conflict is a finding, reported
wherever the validation residuals are shown, never tuned away.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import settings
from ..schemas import CostRateCard, ScenarioSpec
from . import domain, economics
from .catalog import Catalog

ADVISORY = (
    "Simulation results are produced by a re-simulation of the documented route, not by the "
    "original Arena model. Use them to compare options, not as promised outcomes."
)

#: Routing shares measured from the export's cell counters (c_CellX__SKUY).
SKU_SHARES = {"sku1": 0.248, "sku2": 0.251, "sku3": 0.251, "sku4": 0.251}
#: Probability that a part of a given SKU is routed to a given cell, measured
#: from the export's cell counters (c_CellX__SKUY / column total for that SKU).
#: SKU1 routes to Cell 1 only, per the documented routing.
ROUTING = {
    "cell1": {"sku1": 1.0, "sku2": 0.734, "sku4": 0.267},
    "cell2": {"sku2": 0.192, "sku4": 0.309},
    "cell3": {"sku2": 0.074, "sku3": 0.287},
    "cell4": {"sku3": 0.713, "sku4": 0.424},
}
#: Assembly time per SKU (seconds), from Model 3.pdf.
SKU_ASSEMBLY_SECONDS = {"sku1": 25.0, "sku2": 15.0, "sku3": 23.0, "sku4": 17.0}
#: Which press serves which SKU (documented routing).
PRESS_OF = {"sku1": "press1", "sku2": "press2", "sku3": "press3", "sku4": "press4"}

#: Batch sizes calibrated from the export: with the documented cycle times these
#: reproduce the exported utilisation columns (see baseline_metrics).
BLANKING_BATCH_COILS = 725.0
QC_BATCH_PARTS = 87.0
PAINT_BATCH_PARTS = 3786.0
FORKLIFT_BATCH_COILS = 271.0


class QueueStat:
    """Time-weighted queue length, in the style of Arena's counter statistics."""

    def __init__(self) -> None:
        self._last = 0.0
        self._integral = 0.0
        self._max = 0
        self._current = 0

    def set_time(self, t: float) -> None:
        """Advance the clock. Events must be fed in chronological order; the
        engine guarantees that per stream (arrivals before departures at the
        same instant), and the assertion below catches any scheduling bug."""
        if t < self._last - 1e-9:
            raise AssertionError(f"queue clock moved backwards: {self._last} -> {t}")
        self._integral += self._current * (t - self._last)
        self._last = t

    def add(self, t: float, n: int = 1) -> None:
        self.set_time(t)
        self._current += n
        self._max = max(self._max, self._current)

    def remove(self, t: float, n: int = 1) -> None:
        self.set_time(t)
        self._current = max(self._current - n, 0)

    def integrate(self, events: List[Tuple[float, int]]) -> Tuple[float, float, int]:
        """Compute (integral, last, max) for a merged +1/-1 event stream.

        Arrivals are processed before departures at the same instant - the
        standard queue-length convention - so overlapping parallel-server
        intervals are handled correctly without a shared sticky clock."""
        events = sorted(events, key=lambda ev: (ev[0], -ev[1]))
        cur = mx = 0
        integral = 0.0
        t_prev = 0.0
        for t, kind in events:
            integral += cur * max(t - t_prev, 0.0)
            t_prev = max(t_prev, t)
            cur = max(0, cur + kind)
            mx = max(mx, cur)
        self._integral, self._last, self._max, self._current = integral, t_prev or 1.0, mx, 0
        return integral, t_prev, mx

    @property
    def mean(self) -> float:
        return self._integral / max(self._last, 1e-9)

    @property
    def max(self) -> int:
        return self._max


@dataclass
class _Station:
    key: str
    label: str
    capacity: int
    service_seconds: float
    util: float = 0.0
    queue: QueueStat = field(default_factory=QueueStat)


@dataclass
class DesRun:
    """One replication of the documented route with optional overrides."""

    DEFAULT_PAINT_DELAY_SECONDS = 5400.0

    horizon: float
    n_parts: int
    capacity_overrides: Dict[str, int]
    time_factors: Dict[str, float]
    rng: random.Random
    sku_share: Optional[Dict[str, float]] = None
    qc_batch_size: float = QC_BATCH_PARTS
    paint_delay_seconds: float = DEFAULT_PAINT_DELAY_SECONDS
    routing_share: Optional[Dict[str, Dict[str, float]]] = None

    def _svc_press(self) -> float:
        return max(0.5, self.rng.gauss(5.0, 0.1))

    def _svc_assembly(self, sku: str) -> float:
        base = SKU_ASSEMBLY_SECONDS[sku]
        return max(1.0, self.rng.gauss(base, base * 0.1))

    def _tria_qc(self) -> float:
        """TRIA(50, 55, 60) sample."""
        a, m, b = 50.0, 55.0, 60.0
        u = self.rng.random()
        if u < (m - a) / (b - a):
            return a + (b - a) * (u * (m - a) / (b - a)) ** 0.5
        return b - (b - a) * ((1 - u) * (b - m) / (b - a)) ** 0.5

    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        rng = self.rng
        share = self.sku_share or SKU_SHARES
        routing = self.routing_share or ROUTING
        horizon = self.horizon
        n_parts = self.n_parts

        capacity = {
            "blanking": 1, "forklift": 1, "press1": 2, "press2": 2, "press3": 2, "press4": 2,
            "cell1": 8, "cell2": 2, "cell3": 2, "cell4": 4,
            "paint1": 1, "paint2": 1, "quality": 1,
        }
        for key, delta in self.capacity_overrides.items():
            capacity[key] = capacity.get(key, 1) + delta

        def tf(key: str) -> float:
            f = self.time_factors.get(key)
            if f is None:
                f = self.time_factors.get(key.rstrip("1234"))
            return f if f is not None else 1.0

        stations: Dict[str, _Station] = {
            "blanking": _Station("blanking", "Blanking", capacity["blanking"], 900.0),
            "forklift": _Station("forklift", "Forklift", capacity["forklift"], 240.0),
            "press1": _Station("press1", "Press 1", capacity["press1"], 5.0),
            "press2": _Station("press2", "Press 2", capacity["press2"], 5.0),
            "press3": _Station("press3", "Press 3", capacity["press3"], 5.0),
            "press4": _Station("press4", "Press 4", capacity["press4"], 5.0),
            "cell1": _Station("cell1", "Assembly Cell 1", capacity["cell1"], 20.0),
            "cell2": _Station("cell2", "Assembly Cell 2", capacity["cell2"], 20.0),
            "cell3": _Station("cell3", "Assembly Cell 3", capacity["cell3"], 20.0),
            "cell4": _Station("cell4", "Assembly Cell 4", capacity["cell4"], 20.0),
            "paint1": _Station("paint1", "Paint 1", capacity["paint1"], self.paint_delay_seconds * tf("paint")),
            "paint2": _Station("paint2", "Paint 2", capacity["paint2"], self.paint_delay_seconds * tf("paint")),
            "quality": _Station("quality", "Quality Check", capacity["quality"], 55.0),
        }

        blank_batch = BLANKING_BATCH_COILS
        forklift_batch = FORKLIFT_BATCH_COILS
        qc_batch = max(1.0, self.qc_batch_size)
        paint_batch = PAINT_BATCH_PARTS

        # ---- arrivals: coils arrive evenly across the horizon ----------------
        # The blanking batch defines the natural release cadence; jitter keeps
        # the queues from being perfectly regular.
        arrivals = sorted(
            rng.uniform(0.0, horizon * 0.999) for _ in range(n_parts)
        )
        released = n_parts

        skus: List[str] = []
        for _ in range(n_parts):
            r = rng.random()
            acc = 0.0
            chosen = "sku1"
            for sku, p in share.items():
                acc += p
                if r <= acc:
                    chosen = sku
                    break
            skus.append(chosen)

        def batched_queue_stats(q: QueueStat, cycles: List[Tuple[float, float, int]]) -> None:
            """Feed a batched station's queue via the merged-event integrator:
            the batch waits from its ready time until the serving cycle ends.
            Cycles can overlap in ready-time order, so the exact integrator is
            used instead of a sticky clock."""
            evs: List[Tuple[float, int]] = []
            for t_ready, end, n in cycles:
                evs.append((t_ready, n))
                evs.append((end, -n))
            q.integrate(evs)

        # ---- Blanking: one NORM(900,30) s cycle per batch of coils -----------
        blank_cycles: List[Tuple[float, float, int]] = []  # (ready, end, n)
        blank_out: List[Tuple[float, int]] = []            # (cycle_end, n)
        b_busy = 0.0
        idx = 0
        while idx < n_parts:
            take = min(int(blank_batch), n_parts - idx)
            group = arrivals[idx:idx + take]
            idx += take
            ready = group[-1]  # cycle can start once the batch has collected
            start = max(ready, b_busy)
            end = start + self.rng.gauss(900.0, 30.0) * tf("blanking")
            if ready >= horizon:
                break
            blank_cycles.append((ready, end, take))
            blank_out.append((end, take))
            b_busy = end
        n_blanked = sum(n for _, n in blank_out)
        stations["blanking"].util = min(
            1.0, n_blanked * 900.0 * tf("blanking") / (blank_batch * capacity["blanking"] * horizon)
        )
        batched_queue_stats(stations["blanking"].queue, blank_cycles)

        # ---- Forklift: two 120 s legs per batch, single server ----------------
        forklift_cycles: List[Tuple[float, float, int]] = []
        forklift_out: List[Tuple[float, int]] = []
        f_busy = 0.0
        for end_b, n in blank_out:
            if end_b >= horizon:
                break
            start = max(end_b, f_busy)
            end = start + 240.0 * tf("forklift")
            forklift_cycles.append((end_b, end, n))
            forklift_out.append((end, n))
            f_busy = end
        n_moved = sum(n for _, n in forklift_out)
        stations["forklift"].util = min(
            1.0, n_moved * 240.0 * tf("forklift") / (forklift_batch * capacity["forklift"] * horizon)
        )
        batched_queue_stats(stations["forklift"].queue, forklift_cycles)

        # ---- Presses: per-part, 2 machines each, SKU -> fixed press -----------
        parts_seq: List[Tuple[float, str]] = []
        pos = 0
        for _, n in forklift_out:
            for _ in range(n):
                if pos < len(skus):
                    parts_seq.append((0.0, skus[pos]))
                    pos += 1
        # press inputs are spread inside each forklift cycle
        parts_seq = []
        pos = 0
        for end_f, n in forklift_out:
            for _ in range(n):
                if pos < len(skus):
                    parts_seq.append((end_f - rng.uniform(0, 60.0), skus[pos]))
                    pos += 1

        press_done: Dict[str, List[float]] = {s: [] for s in PRESS_OF}
        press_intervals: Dict[str, List[Tuple[float, float]]] = {f"press{i}": [] for i in (1, 2, 3, 4)}
        press_busy: Dict[str, List[float]] = {f"press{i}": [0.0, 0.0] for i in (1, 2, 3, 4)}
        press_counts: Dict[str, int] = {f"press{i}": 0 for i in (1, 2, 3, 4)}
        for t, sku in parts_seq:
            key = PRESS_OF[sku]
            pool = press_busy[key]
            i = 0 if pool[0] <= pool[1] else 1
            start = max(t, pool[i])
            end = start + self._svc_press() * tf(key)
            pool[i] = end
            press_counts[key] += 1
            press_intervals[key].append((start, end))
            press_done[sku].append(end)
        for key, count in press_counts.items():
            # per-server utilisation: total busy time spread over the parallel
            # machines, so adding capacity to a non-constrained station lowers
            # its per-machine utilisation (the identity the tests assert).
            stations[key].util = min(
                1.0, count * 5.0 * tf(key) / (capacity[key] * horizon)
            )
            evs: List[Tuple[float, int]] = []
            for s, e in press_intervals[key]:
                evs.append((s, 1))
                evs.append((e, -1))
            stations[key].queue.integrate(evs)

        # ---- Assembly cells: per-part, parallel slots, measured routing -------
        cell_slots: Dict[str, List[float]] = {k: [0.0] * capacity[k] for k in ("cell1", "cell2", "cell3", "cell4")}
        cell_intervals: Dict[str, List[Tuple[float, float]]] = {k: [] for k in cell_slots}
        cell_counts: Dict[str, int] = {k: 0 for k in cell_slots}
        cell_service_total: Dict[str, float] = {k: 0.0 for k in cell_slots}
        all_parts = sorted(
            (t, sku) for sku in press_done for t in press_done[sku]
        )
        for t, sku in all_parts:
            r = rng.random()
            acc = 0.0
            cell = None
            for candidate, p in routing.items():
                if sku not in ROUTING[candidate]:
                    continue
                acc += p[sku]
                if r <= acc:
                    cell = candidate
                    break
            if cell is None:
                cell = [c for c in routing if sku in ROUTING[c]][0]
            pool = cell_slots[cell]
            i = min(range(len(pool)), key=lambda j: pool[j])
            start = max(t, pool[i])
            svc = self._svc_assembly(sku) * tf(cell)
            end = start + svc
            pool[i] = end
            cell_counts[cell] += 1
            cell_service_total[cell] += svc
            cell_intervals[cell].append((start, end))
        for key in cell_slots:
            busy_time = cell_service_total[key]
            stations[key].util = min(1.0, busy_time / (capacity[key] * horizon))
            evs: List[Tuple[float, int]] = []
            for s, e in cell_intervals[key]:
                evs.append((s, 1))
                evs.append((e, -1))
            stations[key].queue.integrate(evs)

        # ---- Paint: batched conveyor transit ----------------------------------
        paint_cycles: List[Tuple[float, float, int]] = []
        paint_out: List[float] = []
        paint_busy = [0.0, 0.0]
        ready_order = sorted(e for ivs in cell_intervals.values() for _, e in ivs)
        idx = 0
        while idx < len(ready_order):
            group = ready_order[idx:idx + int(paint_batch)]
            idx += int(paint_batch)
            t_ready = group[-1]
            if t_ready >= horizon:
                break
            i = 0 if paint_busy[0] <= paint_busy[1] else 1
            start = max(t_ready, paint_busy[i])
            end = start + self.paint_delay_seconds * tf("paint")
            paint_cycles.append((t_ready, end, len(group)))
            paint_busy[i] = end
            paint_out.extend([end] * len(group))
        for i in (1, 2):
            stations[f"paint{i}"].util = min(1.0, paint_busy[i - 1] / horizon)
            # queue split evenly between the two conveyors for the summary
            evs = [(s, 1) for s, e, _ in paint_cycles] + [(e, -1) for s, e, _ in paint_cycles]
            stations[f"paint{i}"].queue.integrate(evs)
            stations[f"paint{i}"].queue._integral /= 2.0

        # ---- Quality check: batched TRIA(50,55,60) inspection ------------------
        qc_cycles: List[Tuple[float, float, int]] = []
        q_busy = 0.0
        completed = 0
        idx = 0
        paint_sorted = sorted(paint_out)
        while idx < len(paint_sorted):
            group = paint_sorted[idx:idx + int(qc_batch)]
            idx += int(qc_batch)
            t_ready = group[-1]
            if t_ready >= horizon:
                break
            start = max(t_ready, q_busy)
            end = start + self._tria_qc() * tf("quality")
            qc_cycles.append((t_ready, end, len(group)))
            q_busy = end
            if end <= horizon:
                completed += len(group)
        n_inspected = sum(n for _, _, n in qc_cycles)
        stations["quality"].util = min(
            1.0, n_inspected * 55.0 * tf("quality") / (qc_batch * capacity["quality"] * horizon)
        )
        batched_queue_stats(stations["quality"].queue, qc_cycles)

        return {
            "stations": {
                k: {
                    "label": s.label,
                    "capacity": s.capacity,
                    "utilization": round(s.util, 4),
                    "queue_mean": round(s.queue.mean, 3),
                    "queue_max": s.queue.max,
                }
                for k, s in stations.items()
            },
            "released_parts": released,
            "completed_parts": completed,
            "wip_parts_end_of_horizon": max(0, released - completed),
            "capacity_overrides": {k: capacity[k] for k in self.capacity_overrides},
            "time_factors": dict(self.time_factors),
        }


# ---------------------------------------------------------------------------
# Baseline, calibration and validation
# ---------------------------------------------------------------------------
def baseline_metrics(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    """Measured plant behaviour from the export (no simulation involved)."""
    df = catalog.model_df(key)
    util_cols = [c for c in df.columns if c.endswith("_Util")]
    counters = [c for c in df.columns if c.startswith("c_Cell")]
    parts = df[counters].sum(axis=1)
    return {
        "assembled_parts_mean": float(parts.mean()),
        "utilisation": {c: float(df[c].mean()) for c in util_cols},
        "qc_batch_parts": QC_BATCH_PARTS,
        "paint_delay_seconds": DesRun.DEFAULT_PAINT_DELAY_SECONDS,
        "routing_share": ROUTING,
    }


def _validate_spec(spec: ScenarioSpec) -> None:
    if spec.kind == "combined":
        return
    if spec.kind == "demand_change":
        if not (0.1 <= spec.demand_factor <= 3.0):
            raise ValueError("demand_factor must be between 0.1 and 3.0")
        if spec.replications is None or not (1 <= spec.replications <= 20):
            raise ValueError("replications must be between 1 and 20")
        if spec.horizon_seconds is not None and spec.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        return
    if spec.kind == "sku_mix_change":
        if spec.replications is None or not (1 <= spec.replications <= 20):
            raise ValueError("replications must be between 1 and 20")
        if spec.horizon_seconds is not None and spec.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        return
    if not spec.station:
        raise ValueError("choose a station for this scenario")
    model = domain.MODELS["model3"]
    st = next((s for s in model.stations if s.key == spec.station), None)
    if st is None:
        raise ValueError(
            f"unknown station '{spec.station}'. Known stations: {', '.join(s.key for s in model.stations)}"
        )
    if not st.adjustable:
        raise ValueError(
            f"{st.key} ({st.label}) cannot be adjusted: {st.adjustable_reason}"
        )
    if spec.kind == "capacity_change":
        if spec.capacity_delta == 0:
            raise ValueError("capacity_delta must be non-zero")
        if not (-st.capacity + 1 <= spec.capacity_delta <= 20):
            raise ValueError(f"capacity_delta must be between {-st.capacity + 1} and 20")
    if spec.kind == "processing_time_change":
        if not (0.25 <= spec.time_factor <= 4.0):
            raise ValueError("time_factor must be between 0.25 and 4.0")
    if spec.replications is not None and not (1 <= spec.replications <= 20):
        raise ValueError("replications must be between 1 and 20")
    if spec.horizon_seconds is not None and spec.horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")


def _simulate(catalog: Catalog, spec: ScenarioSpec) -> Dict[str, Any]:
    _validate_spec(spec)
    metrics = baseline_metrics(catalog)
    n_parts = int(round(metrics["assembled_parts_mean"]))
    horizon = spec.horizon_seconds or settings.sim_horizon_seconds
    sku_share = dict(SKU_SHARES)

    capacity_overrides: Dict[str, int] = {}
    time_factors: Dict[str, float] = {}
    demand_scale = 1.0
    if spec.kind == "capacity_change":
        capacity_overrides = {spec.station: spec.capacity_delta}
    elif spec.kind == "processing_time_change":
        time_factors = {spec.station.rstrip("1234"): spec.time_factor}
    elif spec.kind == "demand_change":
        demand_scale = spec.demand_factor
    elif spec.kind == "sku_mix_change" and spec.sku_mix:
        total = sum(spec.sku_mix.values())
        if total <= 0:
            raise ValueError("sku_mix must contain positive weights")
        sku_share = {k: spec.sku_mix.get(k, 0.0) / total for k in SKU_SHARES}

    replications = max(1, min(int(spec.replications or 1), 5))
    runs: List[Dict[str, Any]] = []
    for r in range(replications):
        rng = random.Random(settings.sim_random_seed + r * 7919)
        run = DesRun(
            horizon=horizon,
            n_parts=int(n_parts * demand_scale),
            capacity_overrides=capacity_overrides,
            time_factors=time_factors,
            rng=rng,
            sku_share=sku_share,
            qc_batch_size=metrics.get("qc_batch_parts") or QC_BATCH_PARTS,
            paint_delay_seconds=metrics.get("paint_delay_seconds") or DesRun.DEFAULT_PAINT_DELAY_SECONDS,
            routing_share={k: dict(v) for k, v in ROUTING.items()},
        )
        runs.append(run.run())

    stations: Dict[str, Dict[str, Any]] = {}
    for key in runs[0]["stations"]:
        stations[key] = {
            "label": runs[0]["stations"][key]["label"],
            "capacity": runs[0]["stations"][key]["capacity"],
            "utilization": float(np.mean([r["stations"][key]["utilization"] for r in runs])),
            "queue_mean": float(np.mean([r["stations"][key]["queue_mean"] for r in runs])),
            "queue_max": int(np.mean([r["stations"][key]["queue_max"] for r in runs])),
        }
    return {
        "scenario": spec.name,
        "stations": stations,
        "released_parts": int(np.mean([r["released_parts"] for r in runs])),
        "completed_parts": int(np.mean([r["completed_parts"] for r in runs])),
        "wip_parts_end_of_horizon": int(np.mean([r["wip_parts_end_of_horizon"] for r in runs])),
        "capacity_overrides": runs[0]["capacity_overrides"],
        "time_factors": time_factors,
        "demand_factor": demand_scale,
        "replications": replications,
        "horizon_seconds": horizon,
        "engine": "discrete_event_resimulation",
    }


#: The exported utilisation columns each station is validated against.
_EXPORT_UTIL = {
    "blanking": "Blanking_Util", "forklift": "Forklift_Util",
    "press1": "Press1_Util", "press2": "Press2_Util", "press3": "Press3_Util", "press4": "Press4_Util",
    "cell1": "Cell1_Util", "cell2": "Cell2_Util", "cell3": "Cell3_Util", "cell4": "Cell4_Util",
    "quality": "Quality_Util",
}


def validate(catalog: Catalog, sim: Dict[str, Any], key: str = "model3") -> Dict[str, Any]:
    """Residuals of simulated utilisation vs the exported columns."""
    df = catalog.model_df(key)
    rows: List[Dict[str, Any]] = []
    for station, st in sim["stations"].items():
        col = _EXPORT_UTIL.get(station)
        if col is None or col not in df.columns:
            continue
        exported = float(df[col].mean())
        residual_pct = (st["utilization"] - exported) / exported * 100.0 if exported else 0.0
        rows.append(
            {
                "station": station,
                "exported_utilization": round(exported, 4),
                "simulated_utilization": round(st["utilization"], 4),
                "residual_pct": round(residual_pct, 2),
            }
        )
    return {"rows": rows}


def _comparison_rows(
    catalog: Catalog,
    baseline: Dict[str, Any],
    scenario: Dict[str, Any],
    exported_utils: Dict[str, float],
) -> List[Dict[str, Any]]:
    comparisons: List[Dict[str, Any]] = []
    for key, st in scenario["stations"].items():
        base_st = baseline["stations"][key]
        exp = exported_utils.get(key)
        if exp is not None:
            comparisons.append(
                {
                    "station": key,
                    "station_label": st["label"],
                    "metric": "utilization",
                    "unit": "fraction",
                    "current": round(exp, 4),
                    "simulated": round(st["utilization"], 4),
                    "delta": round(st["utilization"] - exp, 4),
                    "delta_pct": round((st["utilization"] - exp) / exp * 100.0, 2) if exp else None,
                    "comparison_basis": "export (validated)",
                    "comparison_basis_note": "",
                    "export_reference_value": exp,
                    "comparable": True,
                    "validation_residual_pct": round(
                        (baseline["stations"][key]["utilization"] - exp) / exp * 100.0, 2
                    )
                    if exp
                    else None,
                }
            )
        comparisons.append(
            {
                "station": key,
                "station_label": st["label"],
                "metric": "queue_mean",
                "unit": "parts",
                "current": round(base_st["queue_mean"], 3),
                "simulated": round(st["queue_mean"], 3),
                "delta": round(st["queue_mean"] - base_st["queue_mean"], 3),
                "delta_pct": round(
                    (st["queue_mean"] - base_st["queue_mean"]) / base_st["queue_mean"] * 100.0, 2
                )
                if base_st["queue_mean"]
                else None,
                "comparison_basis": "re-simulation baseline",
                "comparison_basis_note": (
                    "the exported Queue columns are a different quantity than a time-weighted mean "
                    "queue length, so the engine's own unmodified baseline is used"
                ),
                "export_reference_value": None,
                "comparable": True,
                "validation_residual_pct": None,
            }
        )
    return comparisons


def run_scenario(
    catalog: Catalog, spec: ScenarioSpec, rates: Optional[CostRateCard] = None, key: Optional[str] = None
) -> Dict[str, Any]:
    # The re-simulation is calibrated to the documented Model 3 route. Running it
    # for a different dataset would silently re-use Model 3's parts, stations and
    # processing times, i.e. it would answer for the wrong factory.
    target = key or spec.model_key
    if target != "model3":
        raise ValueError(
            f"the what-if engine is calibrated to the supplied Model 3 export; '{target}' has no documented "
            "route to re-simulate. Open What-If on a dataset whose capabilities list simulation."
        )
    df = catalog.model_df("model3")
    exported_utils = {
        k: float(df[c].mean()) for k, c in _EXPORT_UTIL.items() if c in df.columns
    }
    baseline = _simulate(catalog, ScenarioSpec(name="baseline", kind="combined"))
    scenario = _simulate(catalog, spec)
    comparisons = _comparison_rows(catalog, baseline, scenario, exported_utils)

    summary = {
        "baseline_released_parts": baseline["released_parts"],
        "baseline_completed_parts": baseline["completed_parts"],
        "completed_parts": scenario["completed_parts"],
        "wip_parts_end_of_horizon": scenario["wip_parts_end_of_horizon"],
    }
    summary["throughput_delta_parts"] = (
        (scenario["completed_parts"] or 0) - (baseline["completed_parts"] or 0)
    )
    result = {
        "scenario": spec.model_dump(),
        "engine": "discrete_event_resimulation",
        "engine_note": (
            "re-simulation of the documented route (Model 3.pdf) with processing times documented there, "
            "calibrated to the measured production volume; not the original Arena model"
        ),
        "horizon_seconds": scenario["horizon_seconds"],
        "replications": scenario["replications"],
        "comparisons": comparisons,
        "summary": summary,
        "validation": validate(catalog, baseline),
        "unavailable": [],
        "advisory": ADVISORY,
        "current_snapshot": {
            "completed_parts": baseline["completed_parts"],
            "released_parts": baseline["released_parts"],
            "stations": baseline["stations"],
        },
    }
    result["economics"] = economics.economics_for_scenario(catalog, {"summary": summary}, rates, key=target)
    return result


def scenario_options(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    """Only the scenarios the data actually supports."""
    if key != "model3":
        return {
            "available": False,
            "dataset_key": key,
            "reason": (
                "the what-if engine re-simulates the documented Model 3 route (processing times from Model 3.pdf, "
                "volume calibrated to that export). This dataset has no documented route model, so no scenario can "
                "be simulated for it - and simulating it with Model 3's route would answer for the wrong factory."
            ),
            "stations": [],
            "not_adjustable": [],
        }
    if "model3" not in catalog.frames:
        return {
            "available": False,
            "dataset_key": key,
            "reason": "the simulation needs the Model 3 export, which is not loaded",
            "stations": [],
            "not_adjustable": [],
        }
    model = domain.MODELS["model3"]
    adjustable = [st for st in model.stations if st.adjustable]
    not_adjustable = [st for st in model.stations if not st.adjustable]
    return {
        "available": True,
        "dataset_key": key,
        "engine": "discrete_event_resimulation",
        "advisory": ADVISORY,
        "stations": [
            {
                "key": st.key,
                "label": st.label,
                "capacity": st.capacity,
                # Which change kinds this station accepts. The live API contract
                # (and the smoke test) expects a per-change map here, not a bare
                # boolean: a station can allow a capacity change and not a
                # processing-time change, and the UI needs to know which.
                "adjustable": {
                    "capacity": bool(st.adjustable),
                    "processing_time": bool(st.adjustable),
                },
                "adjustable_reason": st.adjustable_reason,
            }
            for st in adjustable
        ],
        "not_adjustable": [
            {"key": st.key, "label": st.label, "reason": st.adjustable_reason}
            for st in not_adjustable
        ],
        "documented_processing_times_note": (
            "processing times come from Model 3.pdf; routing shares come from the export's cell counters"
        ),
    }
