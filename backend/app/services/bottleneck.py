"""Bottleneck analysis.

Approach
--------
A bottleneck is the capacity-constrained station: the one nearest saturation, the
one parts queue in front of, and the one with the least room to absorb more load.
Three quantities are computed per station and combined into a documented
composite score:

1. **utilisation** - observed in the export (`*_Util` columns).
2. **queue evidence** - mean/max of the station's queue columns, converted to a
   rank across stations so different units are comparable.
3. **load headroom** - ``1/util - 1``: the fractional load increase the station
   can absorb before it is saturated. This is arithmetic on the calibrated model
   (utilisation scales with load because processing times are fixed), so it is a
   defensible statement rather than a model guess.

Stations whose utilisation is not exported are reported as unmeasured, never as
zero. Nothing here involves the LLM.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import analytics, domain
from .catalog import Catalog

log = logging.getLogger("ftm.bottleneck")

#: Documented composite weights (surfaced in the API so they can be challenged).
#: Utilisation dominates because it is the direct, exported measure of how close a
#: station is to its capacity; WIP and capacity are deliberately small enough to
#: act as tie-breakers rather than as drivers. They stay in the score because two
#: stations with equal utilisation are not equally constrained - the one with fewer
#: parallel resources and parts piling up in front of it is the tighter constraint.
WEIGHTS = {"utilisation": 0.80, "queue": 0.12, "capacity": 0.08}


def utilisation_fraction(column: str, value: float) -> tuple:
    """Read a dataset's utilisation column as a fraction, and say how it was read.

    A modelled util column is a fraction (0-1), but plant exports are written the
    way the plant writes them: ``util_pct`` holds 91.0. Taking that verbatim put
    "9094.7% busy" on the Production page, so the unit is resolved from the column
    name and the magnitude, and the assumption is returned with the number instead
    of being applied silently.
    """
    low = str(column).lower()
    if any(token in low for token in ("pct", "percent", "%")):
        return value / 100.0, f"column '{column}' is a percentage (its name states the unit), so it is read as a fraction"
    if 1.0 < value <= 100.0:
        return (
            value / 100.0,
            f"column '{column}' holds values above 1 (mean {value:.1f}), which is only meaningful as a percentage",
        )
    return value, f"column '{column}' is read as a fraction of capacity (mean {value:.3f})"


def station_rows(catalog: Catalog, key: str = "model3") -> List[Dict[str, Any]]:
    """Per-station measured metrics built from the exported columns."""
    df = catalog.model_df(key)
    stats = catalog.population_stats(key)
    sample = catalog.sample_df(key)
    rows: List[Dict[str, Any]] = []

    # Dynamic fallback for user datasets
    spec = domain.model_for(key, catalog=catalog)
    
    # If it's an uploaded dataset and has a station column (long format)
    if key.startswith("user:"):
        profile = catalog.profile_map(key)
        m = profile.get("manufacturing", {}) or {}
        station_cols = m.get("stations", [])
        process_vars = m.get("process_vars", [])
        
        if station_cols:
            station_col = station_cols[0]
            unique_stations = df[station_col].dropna().unique().tolist()
            # Try to infer metric columns
            util_col = next((c for c in process_vars if "util" in c.lower()), None)
            queue_col = next((c for c in process_vars if "queue" in c.lower() or "wip" in c.lower()), None)
            cap_col = next((c for c in process_vars if "capacity" in c.lower()), None)
            
            # Using groupby for long format statistics
            group_stats = df.groupby(station_col).mean(numeric_only=True)
            
            for i, st_name in enumerate(unique_stations):
                st_key = str(st_name).lower()
                row = {
                    "station": st_key,
                    "label": str(st_name),
                    "stage": str(st_name),
                    "units": "",
                    "unavailable": [],
                }
                
                row_stats = group_stats.loc[st_name] if st_name in group_stats.index else {}
                
                if cap_col and cap_col in row_stats:
                    row["capacity"] = float(row_stats[cap_col])
                    row["capacity_source"] = "dataset"
                else:
                    row["capacity"] = None
                    row["capacity_source"] = "not_available"
                
                if util_col and util_col in row_stats:
                    raw_util = float(row_stats[util_col])
                    fraction, note = utilisation_fraction(util_col, raw_util)
                    row["utilization"] = fraction
                    row["utilization_raw_mean"] = raw_util
                    row["utilization_note"] = note
                    row["utilization_source"] = "dataset"
                    row["utilization_column"] = util_col
                else:
                    row["utilization"] = None
                    row["utilization_source"] = "not_available"
                    row["unavailable"].append("utilisation")
                    
                if queue_col and queue_col in row_stats:
                    row["queue_mean"] = float(row_stats[queue_col])
                    row["queue_max"] = float(df[df[station_col] == st_name][queue_col].max())
                    row["queue_columns"] = [queue_col]
                    row["wip_evidence"] = row["queue_mean"]
                    row["wip_evidence_source"] = "dataset"
                else:
                    row["queue_mean"] = None
                    row["queue_max"] = None
                    row["queue_columns"] = []
                    row["unavailable"].append("queue")
                    
                if row["utilization"] is None:
                    row["headroom"] = None
                elif row["utilization"] >= 0.999:
                    row["headroom"] = 0.0
                else:
                    row["headroom"] = 1.0 / max(row["utilization"], 1e-9) - 1.0
                    
                row["processed_units_mean"] = None
                rows.append(row)
            return rows

    for station in spec.stations:
        row: Dict[str, Any] = {
            "station": station.key,
            "label": station.label,
            "stage": station.stage,
            "units": station.units,
            "capacity": station.capacity,
            "capacity_source": station.capacity_source,
            "time_source": station.time_source,
            "time_note": station.time_note,
            "processing_time_seconds": station.processing_time_seconds,
            "unavailable": [],
        }
        if station.utilization_column and station.utilization_column in df.columns:
            col = station.utilization_column
            row["utilization"] = float(stats.loc[col, "mean"])
            row["utilization_source"] = "dataset"
            row["utilization_column"] = col
            row["utilization_min"] = float(stats.loc[col, "min"])
            row["utilization_max"] = float(stats.loc[col, "max"])
        else:
            row["utilization"] = None
            row["utilization_source"] = "not_available"
            row["unavailable"].append("utilisation")

        queue_cols = [c for c in station.queue_columns if c in df.columns]
        if queue_cols:
            means = {c: float(stats.loc[c, "mean"]) for c in queue_cols}
            maxima = {c: float(stats.loc[c, "max"]) for c in queue_cols}
            row["queue_by_column"] = means
            row["queue_max_by_column"] = maxima
            row["queue_mean"] = float(np.mean(list(means.values())))
            row["queue_max"] = float(np.max(list(maxima.values())))
            row["queue_columns"] = queue_cols
        else:
            row["queue_mean"] = None
            row["queue_max"] = None
            row["queue_columns"] = []
            row["unavailable"].append("queue")

        wip_cols = [c for c in station.wip_columns if c in df.columns]
        row["wip"] = float(np.mean([float(stats.loc[c, "mean"]) for c in wip_cols])) if wip_cols else None

        # Waiting parts for the assembly cells are held in the warehouse buffers,
        # not in CellX_Queue (which is 0 in every row). Use the buffers as the WIP
        # evidence for cells, and say so, instead of reporting a misleading zero.
        row["wip_evidence"] = row["queue_mean"]
        row["wip_evidence_source"] = "exported queue columns" if queue_cols else None
        if (row.get("queue_mean") or 0.0) <= 1e-9:
            buffers = [c for c in df.columns if c.startswith("Warehouse") and c.endswith("Queue")]
            if buffers:
                row["wip_evidence"] = float(np.mean([float(stats.loc[c, "mean"]) for c in buffers]))
                row["wip_evidence_source"] = (
                    "warehouse buffer queues (Warehouse1..4_Queue): the station's own queue column is 0 in "
                    "every row of this export"
                )
                row["wip_evidence_note"] = (
                    "The export does not document which buffer feeds which cell, so this is plant-level WIP "
                    "evidence shared between the four assembly cells."
                )

        counters = [c for c in station.counter_columns if c in sample.columns]
        row["counter_columns"] = counters
        if counters:
            row["processed_units_mean"] = float(sample[counters].fillna(0.0).sum(axis=1).mean())
        else:
            row["processed_units_mean"] = None

        if row["utilization"] is None:
            row["headroom"] = None
        elif row["utilization"] >= 0.999:
            row["headroom"] = 0.0
        else:
            row["headroom"] = 1.0 / max(row["utilization"], 1e-9) - 1.0

        rows.append(row)
    return rows


def _queue_rank(rows: List[Dict[str, Any]]) -> None:
    measured = [r for r in rows if r.get("wip_evidence") is not None]
    if not measured:
        return
    values = np.array([r["wip_evidence"] for r in measured], dtype="float64")
    order = values.argsort().argsort().astype("float64")
    denom = max(len(measured) - 1, 1)
    for r, rank in zip(measured, order):
        r["queue_rank_score"] = float(rank / denom)
    for r in rows:
        r.setdefault("queue_rank_score", None)


def _capacity_constraint(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        cap = r.get("capacity")
        if cap is None or cap <= 0:
            r["capacity_score"] = None
        else:
            # fewer parallel resources = less scheduling flexibility
            r["capacity_score"] = float(1.0 / (1.0 + cap))
    known = [r["capacity_score"] for r in rows if r.get("capacity_score") is not None]
    if not known:
        return
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1.0
    for r in rows:
        if r.get("capacity_score") is not None:
            r["capacity_score"] = (r["capacity_score"] - lo) / span


def rank_bottlenecks(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    rows = station_rows(catalog, key)
    _queue_rank(rows)
    _capacity_constraint(rows)

    measured_utils = [r["utilization"] for r in rows if r.get("utilization") is not None]
    max_util = max(measured_utils) if measured_utils else 1.0

    for r in rows:
        util = r.get("utilization")
        if util is None:
            r["bottleneck_score"] = None
            r["rank"] = None
            r["evidence"] = [
                f"utilisation is not exported for {r['label']} "
                f"({'/'.join(r['unavailable']) if r['unavailable'] else 'no column'})"
            ]
            continue
        parts = {"utilisation": util / max_util if max_util else util}
        if r.get("queue_rank_score") is not None:
            parts["queue"] = r["queue_rank_score"]
        if r.get("capacity_score") is not None:
            parts["capacity"] = r["capacity_score"]
        total_weight = sum(WEIGHTS[k] for k in parts)
        score = sum(WEIGHTS[k] * v for k, v in parts.items()) / (total_weight or 1.0)
        r["bottleneck_score"] = float(score)
        r["score_components"] = {k: float(v) for k, v in parts.items()}
        r["utilisation_normalised"] = float(parts["utilisation"])
        r["utilisation_reference_max"] = float(max_util)

        evidence = [
            f"utilisation {util:.3f}" + (f" (capacity {r['capacity']:.0f} {r['units']})" if r.get("capacity") else ""),
        ]
        if r.get("headroom") is not None:
            evidence.append(f"absorbs a further {r['headroom'] * 100:.1f}% load increase before saturation")
        if r.get("wip_evidence") is not None and r.get("wip_evidence_source"):
            evidence.append(
                f"WIP evidence {r['wip_evidence']:.1f} parts ({r['wip_evidence_source']})"
            )
            if r.get("wip_evidence_note"):
                evidence.append(r["wip_evidence_note"])
        if r.get("processed_units_mean"):
            evidence.append(f"mean units processed per run {r['processed_units_mean']:,.0f}")
        r["evidence"] = evidence

    ranked = [r for r in rows if r["bottleneck_score"] is not None]
    ranked.sort(key=lambda r: -r["bottleneck_score"])
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return {
        "model_key": key,
        "weights": WEIGHTS,
        "ranking": ranked,
        "unmeasured": [r for r in rows if r["bottleneck_score"] is None],
        "method": (
            "composite of utilisation normalised to the highest measured station (0.80), WIP/queue rank "
            "across stations (0.12) and capacity constraint (0.08). Utilisation dominates deliberately: the "
            "station with the highest long-run utilisation is the binding constraint, and the other two terms "
            "only break near-ties. Weights, components and the normalisation reference are all returned so the "
            "ranking can be challenged. For the assembly cells the WIP term uses the warehouse buffer queues "
            "because CellX_Queue is 0 in every exported row."
        ),
        "utilisation_reference_max": max_util,
    }


def capacity_headroom(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    """How much extra load each station can take before it saturates."""
    rows = [r for r in station_rows(catalog, key) if r.get("utilization")]
    rows.sort(key=lambda r: r["utilization"], reverse=True)
    out = [
        {
            "station": r["station"],
            "label": r["label"],
            "utilisation": r["utilization"],
            "headroom_factor": r["headroom"],
            "headroom_pct": None if r["headroom"] is None else r["headroom"] * 100.0,
            "basis": (
                "utilisation scales linearly with load because the documented processing times are "
                "independent of load, so load_headroom = 1/util - 1"
            ),
        }
        for r in rows
    ]
    binding = out[0] if out else None
    return {
        "model_key": key,
        "stations": out,
        "first_station_to_saturate": binding["station"] if binding else None,
        "plant_headroom_pct": binding["headroom_pct"] if binding else None,
        "note": (
            "This is arithmetic on the exported utilisation columns plus the documented capacities, "
            "not a simulation result."
        ),
    }


def production_snapshot(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    spec = domain.model_for(key, catalog=catalog)
    df = catalog.model_df(key)
    ranking = rank_bottlenecks(catalog, key)
    headroom = capacity_headroom(catalog, key)

    throughput_total = None
    throughput_unit = ""
    if key == "model3":
        parts = catalog._derived.get("model3_parts", {}).get("total_parts")
        if parts is not None:
            throughput_total = float(parts.mean())
            throughput_unit = "assembled parts per run (sum of exported cell counters)"
    elif key == "model2" and "Entities Out" in df.columns:
        throughput_total = float(catalog.population_stats(key).loc["Entities Out", "mean"])
        throughput_unit = "assembled entities out per run"
    elif key == "model1" and "Parts per hour" in df.columns:
        throughput_total = float(catalog.population_stats(key).loc["Parts per hour", "mean"])
        throughput_unit = "parts per hour"

    wip_cols = [c for c in spec.wip_columns if c in df.columns]
    queue_cols = analytics.model3_queue_columns(df) if key == "model3" else []
    wip_total = None
    if wip_cols:
        wip_total = float(np.mean([float(catalog.population_stats(key).loc[c, "mean"]) for c in wip_cols]))
    elif queue_cols:
        wip_total = float(np.mean([float(catalog.population_stats(key).loc[c, "mean"]) for c in queue_cols]))

    unavailable: List[str] = []
    if key == "model3":
        unavailable += [
            "cycle time per part: the export contains no per-entity cycle-time column",
            "downtime: no failure/downtime column is exported by any model",
            "changeovers: no setup or changeover column is exported",
            "scrap / rework: the Arena model contains scrap logic but exports no scrap or rework counts",
        ]

    return {
        "model_key": key,
        "model_label": spec.label,
        "horizon_seconds": float(catalog.provenance()["horizon"]["seconds"]),
        "throughput_total": throughput_total,
        "throughput_unit": throughput_unit,
        "wip_total": wip_total,
        "wip_definition": (
            "mean of the exported stored-part counters" if wip_cols else
            "mean across the exported queue columns (the export has no separate WIP column)"
        ),
        "stations": [r for r in ranking["ranking"]] + ranking["unmeasured"],
        "bottleneck_candidates": ranking["ranking"][:4],
        "ranking_method": ranking["method"],
        "ranking_weights": ranking["weights"],
        "headroom": headroom,
        "calibration": analytics.calibration_report(catalog, key),
        "utilisation_profile": [
            {
                "station": r["label"],
                "stage": r["stage"],
                "utilization": r.get("utilization"),
                "capacity": r.get("capacity"),
                "queue_mean": r.get("queue_mean"),
                "wip_evidence": r.get("wip_evidence"),
                "rank": r.get("rank"),
                "bottleneck_score": r.get("bottleneck_score"),
            }
            for r in ranking["ranking"]
        ],
        "unavailable": unavailable,
    }
