"""Forensic analysis: case building and the failure-propagation graph.

A *case* is a defined group of replications (e.g. the lowest-output 1%) contrasted
against the rest of the population, station by station. The output is an ordered
divergence timeline plus evidence, always carrying the causal caveat.

The *propagation graph* follows the documented chain:

    process problem -> station -> WIP/queue -> defect -> production -> economic

Nodes and edges are labelled observed / assumed / unavailable; assumed edges come
only from engineer-declared linkage assumptions, never invented here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import settings
from . import analytics, bottleneck, domain
from .catalog import Catalog

log = logging.getLogger("ftm.forensics")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Uploaded datasets: their own cases, from their own columns
# ---------------------------------------------------------------------------
def _user_manufacturing(catalog: Catalog, key: str) -> Dict[str, Any]:
    return (catalog.profile_map(key) or {}).get("manufacturing", {}) or {}


def _user_columns(catalog: Catalog, key: str, df) -> tuple:
    """(station column, numeric process columns) that actually exist in the frame."""
    m = _user_manufacturing(catalog, key)
    stations = [c for c in (m.get("stations") or []) if c in df.columns]
    process = [
        c
        for c in (m.get("process_vars") or [])
        if c in df.columns and np.issubdtype(df[c].dtype, np.number)
    ]
    return (stations[0] if stations else None), process


def _primary_process(process: List[str]) -> Optional[str]:
    """The process column a shift group is judged by (utilisation first, then the first)."""
    for needle in ("util", "cycle_time", "downtime", "queue", "wip", "defect", "scrap"):
        for col in process:
            if needle in col.lower():
                return col
    return process[0] if process else None


def _user_cases(catalog: Catalog, key: str) -> List[Dict[str, Any]]:
    df = catalog.model_df(key)
    station_col, process = _user_columns(catalog, key, df)
    cases: List[Dict[str, Any]] = []
    primary = _primary_process(process)
    if station_col and primary:
        means = df.groupby(station_col)[primary].mean().sort_values(ascending=False)
        plant = float(df[primary].mean())
        for value in list(means.index)[:3]:
            cases.append(
                {
                    "case_id": f"group:station:{value}",
                    "case_id_short": f"station:{value}",
                    "label": f"Behaviour of {value}",
                    "kind": "group",
                    "scope": "group",
                    "description": f"{primary} mean {means[value]:.3f} vs plant mean {plant:.3f}",
                    "summary": f"{int((df[station_col] == value).sum())} rows for {value}",
                }
            )
    if not cases and primary:
        q = df[primary].quantile(0.95)
        cases.append(
            {
                "case_id": f"group:high:{primary}",
                "case_id_short": f"high:{primary}",
                "label": f"Rows with the highest {primary}",
                "kind": "group",
                "scope": "group",
                "description": f"top 5% of {primary} (>= {q:.3f})",
                "summary": f"{int((df[primary] >= q).sum())} rows in the top 5% of {primary}",
            }
        )
    return cases


def _select_user_group(catalog: Catalog, key: str, case_id: str):
    df = catalog.model_df(key)
    station_col, process = _user_columns(catalog, key, df)
    if case_id.startswith("group:station:"):
        value = case_id.split("group:station:", 1)[1]
        if station_col is None or value not in set(df[station_col].astype(str)):
            raise ValueError(f"unknown station '{value}' in this dataset")
        mask = df[station_col].astype(str) == value
        return mask, f"{station_col} == {value}"
    if case_id.startswith("group:high:"):
        column = case_id.split("group:high:", 1)[1]
        if column not in df.columns:
            raise ValueError(f"unknown column '{column}' in this dataset")
        q = df[column].quantile(0.95)
        return (df[column] >= q), f"{column} >= P95 ({q:.3f})"
    raise ValueError(f"unknown case '{case_id}' for this dataset")


def _build_user_case(catalog: Catalog, key: str, case_id: str) -> Dict[str, Any]:
    """A group contrast over the dataset's own process columns."""
    df = catalog.model_df(key)
    mask, selection = _select_user_group(catalog, key, case_id)
    station_col, process = _user_columns(catalog, key, df)
    stats = catalog.population_stats(key)

    events: List[Dict[str, Any]] = []
    for col in process:
        baseline = float(stats.loc[col, "mean"]) if col in stats.index else float(df[col].mean())
        std = float(stats.loc[col, "std"]) if col in stats.index else float(df[col].std())
        std = std or 1.0
        case_mean = float(df.loc[mask, col].mean())
        z = (case_mean - baseline) / std
        if abs(z) < 0.5:
            continue
        station = (
            str(df.loc[mask, station_col].mode().iloc[0])
            if station_col
            else (case_id.split(":")[-1])
        )
        events.append(
            {
                "order": len(events) + 1,
                "station": station,
                "stage": station,
                "label": station,
                "metric": col,
                "value": round(case_mean, 4),
                "baseline": round(baseline, 4),
                "z": round(float(z), 2),
                "delta_pct": round((case_mean - baseline) / baseline * 100.0, 2) if baseline else None,
                "severity": "high" if abs(z) >= 2.5 else ("moderate" if abs(z) >= 1.0 else "low"),
                "evidence": [f"{col}: case mean {case_mean:.3f} vs population {baseline:.3f} (z = {z:.2f})"],
            }
        )
    events.sort(key=lambda e: -abs(e["z"]))
    for i, e in enumerate(events, start=1):
        e["order"] = i

    primary = _primary_process(process)
    outcome = {
        "metric": primary,
        "case_mean": round(float(df.loc[mask, primary].mean()), 4) if primary else None,
        "population_mean": round(float(df[primary].mean()), 4) if primary else None,
    }
    return {
        "case_id": case_id,
        "label": case_id.replace("group:", "").replace("-", " "),
        "scope": "group",
        "statistic": "mean of each detected process column inside the group vs the population",
        "selection": {"rule": selection, "n": int(mask.sum())},
        "baseline_definition": "mean across every row of this dataset",
        "outcome": outcome,
        "timeline": events,
        "first_divergence": events[0] if events else None,
        "co_occurring": events[1:4],
        "evidence": [
            {
                "claim": f"{e['metric']} diverges by z = {e['z']}",
                "value": e["z"],
                "unit": "z-score",
                "source": "this dataset's own columns",
                "strength": "strong" if abs(e["z"]) >= 2.5 else "moderate",
            }
            for e in events[:5]
        ],
        "confidence": min(0.85, 0.4 + 0.1 * len(events)),
        "confidence_basis": [
            f"{len(events)} measured columns diverge beyond the documented z threshold",
            "each divergence is measured against this dataset's own distribution",
        ],
        "limitations": [
            "Rows are not ordered in time unless the dataset states a timestamp; this is a group contrast, not a timeline.",
            "The dataset carries no defect label, so a divergent column cannot be named as a physical defect.",
        ],
        "causal_caveat": (
            "Divergence is association, not causation: no intervention or randomisation exists in the upload, "
            "so the ordering of these events is descriptive only."
        ),
        "data_quality": [],
    }


def list_cases(catalog: Catalog, key: str = "model3") -> List[Dict[str, Any]]:
    """The case catalogue: groups worth investigating, for *this* dataset."""
    if key.startswith("user:"):
        return _user_cases(catalog, key)
    cases: List[Dict[str, Any]] = []
    # Only the Model 3 frame has the derived assembled-parts series. Using it for
    # another dataset (an upload, or model1/2) silently contrasted the wrong rows.
    parts = catalog._derived.get("model3_parts", {}).get("total_parts") if key == "model3" else None
    if parts is None:
        # model1/2: use their throughput columns
        df = catalog.model_df(key)
        col = next((c for c in ("Total parts", "Entities Out") if c in df.columns), None)
        if col is None:
            return []
        parts = df[col]

    cases.append(
        {
            "case_id": "group:lowest-output-1pct",
            "label": "Lowest-output 1% of replications",
            "scope": "group",
            "summary": f"{int((parts <= parts.quantile(0.01)).sum())} replications in the bottom 1% of assembled parts",
        }
    )
    cases.append(
        {
            "case_id": "group:highest-output-1pct",
            "label": "Highest-output 1% of replications",
            "scope": "group",
            "summary": f"{int((parts >= parts.quantile(0.99)).sum())} replications in the top 1% of assembled parts",
        }
    )
    cases.append(
        {
            "case_id": "group:longest-press4-queue",
            "label": "Replications with the longest Press 4 queue",
            "scope": "group",
            "summary": "top 1% by mean Press4_Queue length",
        }
    )
    return cases


def _select_group(catalog: Catalog, key: str, case_id: str):
    if key.startswith("user:"):
        return _select_user_group(catalog, key, case_id)
    df = catalog.model_df(key)
    parts = catalog._derived.get("model3_parts", {}).get("total_parts") if key == "model3" else None
    if parts is None:
        col = next((c for c in ("Total parts", "Entities Out") if c in df.columns), None)
        parts = df[col] if col else df.iloc[:, 0]
    if case_id == "group:lowest-output-1pct":
        return parts <= parts.quantile(0.01), f"assembled parts <= P01 ({parts.quantile(0.01):.1f})"
    if case_id == "group:highest-output-1pct":
        return parts >= parts.quantile(0.99), f"assembled parts >= P99 ({parts.quantile(0.99):.1f})"
    if case_id == "group:longest-press4-queue" and "Press4_Queue" in df.columns:
        q = df["Press4_Queue"].quantile(0.99)
        return df["Press4_Queue"] >= q, f"Press4_Queue >= P99 ({q:.1f})"
    raise ValueError(f"unknown case '{case_id}'")


def build_case(catalog: Catalog, key: str, case_id: str) -> Dict[str, Any]:
    if key.startswith("user:"):
        return _build_user_case(catalog, key, case_id)
    df = catalog.model_df(key)
    mask, selection = _select_group(catalog, key, case_id)
    stats = catalog.population_stats(key)

    util_cols = [c for c in df.columns if c.endswith("_Util")]
    queue_cols = analytics.model3_queue_columns(df)

    events: List[Dict[str, Any]] = []
    for col in util_cols + queue_cols:
        baseline = float(stats.loc[col, "mean"])
        case_mean = float(df.loc[mask, col].mean())
        std = float(stats.loc[col, "std"]) or 1.0
        z = (case_mean - baseline) / std
        if abs(z) < 0.5:
            continue
        stage = col.rsplit("_", 1)[0] if col.endswith("_Util") else col.replace("_Queue", "")
        events.append(
            {
                "order": len(events) + 1,
                "station": stage,
                "stage": stage,
                "metric": col,
                "value": round(case_mean, 4),
                "baseline": round(baseline, 4),
                "z": round(float(z), 2),
                "severity": "high" if abs(z) >= 2.5 else ("moderate" if abs(z) >= 1.0 else "low"),
                "evidence": [
                    f"{col}: case mean {case_mean:.3f} vs population {baseline:.3f} (z = {z:.2f})"
                ],
            }
        )
    events.sort(key=lambda e: -abs(e["z"]))
    for i, e in enumerate(events, start=1):
        e["order"] = i

    first = events[0] if events else None
    co_occurring = events[1:4]
    parts_all = _parts_for(catalog, key, df)
    outcome = {
        "metric": "assembled parts",
        "case_mean": round(float(parts_all[mask].mean()), 2),
        "population_mean": round(float(parts_all.mean()), 2),
    }

    return {
        "case_id": case_id,
        "label": case_id.replace("group:", "").replace("-", " "),
        "scope": "group",
        "statistic": "mean of each exported column inside the group vs the population",
        "selection": {"rule": selection, "n": int(mask.sum())},
        "baseline_definition": "mean across all replications in the export",
        "outcome": outcome,
        "timeline": events,
        "first_divergence": first,
        "co_occurring": co_occurring,
        "evidence": [
            {
                "claim": f"{e['station']} diverges by z = {e['z']}",
                "value": e["z"],
                "unit": "z-score",
                "source": "exported columns",
                "strength": "strong" if abs(e["z"]) >= 2.5 else "moderate",
            }
            for e in events[:5]
        ],
        "confidence": min(0.9, 0.5 + 0.1 * len(events)),
        # A list, because the confidence rests on several separate things and the
        # UI (and the live contract test) renders them one by one.
        "confidence_basis": [
            f"{len(events)} station metrics diverge beyond the documented z threshold",
            "each divergence is measured against this dataset's own distribution",
            "the route order of the divergences is the documented process order, not an observed sequence",
        ],
        "limitations": [
            "Rows are independent replications, so this is a group contrast, not a timeline.",
            "No wall-clock ordering can be claimed from the export; the route order of the divergences "
            "is the documented process order, not an observed sequence.",
        ],
        "causal_caveat": (
            "Divergence is association, not causation: the export has no intervention or "
            "randomisation, so the ordering of these events is descriptive only."
        ),
        "data_quality": [i.message for i in catalog.issues[:3]],
    }


def _parts_for(catalog: Catalog, key: str, df):
    parts = None
    if key == "model3":
        parts = getattr(catalog, "_derived", {}).get("model3_parts", {}).get("total_parts")
    if parts is None:
        col = next((c for c in ("Total parts", "Entities Out") if c in df.columns), None)
        return df[col] if col else pd_series_stub(df)
    return parts


def pd_series_stub(df):
    import pandas as pd

    return pd.Series(0.0, index=df.index)


# ---------------------------------------------------------------------------
# Station baseline
# ---------------------------------------------------------------------------
def station_baseline(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    stats = catalog.population_stats(key)
    stations: Dict[str, Any] = {}
    for col in stats.index:
        if col.endswith("_Util") or col.endswith("_Queue"):
            station = col.rsplit("_", 1)[0]
            entry = stations.setdefault(station, {"metrics": {}})
            entry["metrics"][col] = {
                "mean": round(float(stats.loc[col, "mean"]), 4),
                "std": round(float(stats.loc[col, "std"]), 4),
                "min": round(float(stats.loc[col, "min"]), 4),
                "max": round(float(stats.loc[col, "max"]), 4),
            }
    return {"model_key": key, "stations": stations}


def severity_from_z(z: Optional[float]) -> str:
    if z is None:
        return "unknown"
    z = abs(z)
    if z >= 2.5:
        return "high"
    if z >= 1.0:
        return "moderate"
    return "low"


# ---------------------------------------------------------------------------
# Propagation graph
# ---------------------------------------------------------------------------
def _user_propagation_graph(
    catalog: Catalog, key: str, assumptions: List[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    """An uploaded dataset's own graph: measured stations, no invented order.

    The file measures station states but states no route order and carries no
    timestamps, so the stations are shown as observed nodes and **no** edge is
    drawn between them. Defect nodes are absent because a CSV has no image labels,
    and the economic node stays unavailable until a rate card exists.
    """
    rows = bottleneck.station_rows(catalog, key)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for row in rows:
        util = row.get("utilization")
        metrics: Dict[str, Any] = {}
        if util is not None:
            metrics["utilisation"] = round(float(util), 4)
        if row.get("queue_mean") is not None:
            metrics["queue_mean"] = round(float(row["queue_mean"]), 3)
        if row.get("capacity") is not None:
            metrics["capacity"] = row["capacity"]
        nodes.append(
            {
                "id": f"station_{str(row['station']).replace(' ', '_')}",
                "label": row.get("label") or row.get("station"),
                "kind": "state",
                "domain": "dataset",
                "status": "observed" if util is not None else "unavailable",
                "metrics": metrics,
                "evidence": [
                    {
                        "claim": f"measured utilisation {util:.3f}",
                        "value": util,
                        "unit": "fraction",
                        "source": row.get("utilization_column") or "dataset column",
                        "strength": "strong",
                    }
                ]
                if util is not None
                else [],
                "notes": [] if util is not None else [f"no utilisation column for {row.get('label')}"],
            }
        )

    if not nodes:
        return {
            "nodes": [],
            "edges": [],
            "assumed_edges": 0,
            "skipped_assumptions": [],
            "legend": _GRAPH_LEGEND,
            "linkage_notice": "No propagation path can be established from the available data.",
            "limitations": [
                "This dataset has no station column, so no propagation path can be established from the available data.",
            ],
        }

    output_col = next(
        (
            col
            for col in (catalog.profile_map(key).get("manufacturing", {}).get("process_vars") or [])
            if any(word in col.lower() for word in ("throughput", "output", "capacity"))
        ),
        None,
    )
    nodes.append(
        {
            "id": "production_outcome",
            "label": "Production output",
            "kind": "outcome",
            "domain": "dataset",
            "status": "observed" if output_col else "unavailable",
            "metrics": {},
            "evidence": [],
            "notes": [] if output_col else ["this dataset exports no throughput or output column"],
        }
    )
    for row in rows:
        if row.get("capacity") is None and row.get("utilization") is None:
            continue
        edges.append(
            {
                "id": f"e_measured_{str(row['station']).replace(' ', '_')}_output",
                "source": f"station_{str(row['station']).replace(' ', '_')}",
                "target": "production_outcome",
                "relation": "contributes_to",
                "status": "observed",
                "method": "both quantities are measured on the same rows of this dataset",
                "evidence": [],
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "assumed_edges": 0,
        "skipped_assumptions": [],
        "legend": _GRAPH_LEGEND,
        "linkage_notice": (
            "The uploaded dataset measures station states but states no route order and carries no timestamps, "
            "so no station-to-station propagation path can be established from the data. Each station is shown "
            "as a measured node; defect classes are absent because a CSV has no image labels."
        ),
        "limitations": [
            "No edge is drawn between stations: their order is not in this dataset.",
            "No defect node exists for an uploaded CSV, so nothing can be linked to a defect class.",
            "Economic impact stays unavailable until a rate card is stored for this dataset.",
        ],
    }


_GRAPH_LEGEND = {
    "observed": "measured in this dataset",
    "assumed": "declared by an engineer or structural (documented route)",
    "unavailable": "not measurable with the current data",
}


def propagation_graph(
    catalog: Catalog, key: str, assumptions: List[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    if key.startswith("user:"):
        # An upload has no documented route; a graph that invented one (or reused
        # Model 3's) would be a fabricated result. It gets its own measured view.
        return _user_propagation_graph(catalog, key, assumptions)
    spec = domain.model_for(key, catalog=catalog)
    if spec is None:
        return {
            "nodes": [],
            "edges": [],
            "legend": {},
            "linkage_notice": "no documented model for this dataset",
            "limitations": ["The propagation graph needs a documented station route."],
        }
    assumptions = assumptions or []

    rows = bottleneck.station_rows(catalog, key)
    by_station = {r["station"]: r for r in rows}
    stats = catalog.population_stats(key)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    process_node = {
        "id": "process_problem",
        "label": "Process problem",
        "kind": "process",
        "domain": "simulation",
        "status": "assumed",
        "metrics": {},
        "evidence": [],
        "notes": ["Starting point of the chain; what actually caused it is not observable in the export."],
    }
    nodes.append(process_node)

    route = sorted(spec.stations, key=lambda s: (s.route_index, s.key))
    if not route:
        # A documented model with no stations cannot be drawn as a route; returning
        # an empty graph is honest, ``route[0]`` below would be a 500.
        return {
            "nodes": [],
            "edges": [],
            "assumed_edges": 0,
            "skipped_assumptions": [],
            "legend": _GRAPH_LEGEND,
            "linkage_notice": "No propagation path can be established from the available data.",
            "limitations": ["This dataset documents no station route, so no propagation path can be drawn."],
        }
    for st in route:
        row = by_station.get(st.key, {})
        util = row.get("utilization")
        metrics: Dict[str, Any] = {}
        if util is not None:
            metrics["utilization"] = round(util, 4)
        if row.get("queue_mean") is not None:
            metrics["queue_mean"] = round(row["queue_mean"], 3)
        if row.get("wip_evidence") is not None:
            metrics["wip_evidence"] = round(row["wip_evidence"], 3)
        nodes.append(
            {
                "id": f"stage_{st.key}",
                "label": st.label,
                "kind": "state",
                "domain": "simulation",
                "status": "observed" if util is not None else "unavailable",
                "metrics": metrics,
                "evidence": [
                    {
                        "claim": f"exported utilisation {util:.3f}",
                        "value": util,
                        "unit": "fraction",
                        "source": st.utilization_column or "",
                        "strength": "strong",
                    }
                ]
                if util is not None
                else [],
                "notes": [] if util is not None else [f"utilisation not exported for {st.label}"],
            }
        )

    # Edges: process -> first stage, stage -> stage along the route.
    edges.append(
        {
            "id": "e_process_first",
            "source": "process_problem",
            "target": f"stage_{route[0].key}",
            "relation": "disrupts",
            "status": "observed",
            "method": (
                "structural edge: the route order is documented, so a problem anywhere upstream "
                "necessarily appears at the first stage; the specific entry point is not observable"
            ),
            "evidence": [],
        }
    )
    for a, b in zip(route, route[1:]):
        edges.append(
            {
                "id": f"e_{a.key}_{b.key}",
                "source": f"stage_{a.key}",
                "target": f"stage_{b.key}",
                "relation": "feeds",
                "status": "observed",
                "method": "documented route order",
                "evidence": [],
            }
        )

    # WIP node per station that has queue evidence.
    wip_nodes = []
    for st in route:
        row = by_station.get(st.key, {})
        if row.get("wip_evidence") is None:
            continue
        node_id = f"wip_{st.key}"
        wip_nodes.append(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": f"Queue at {st.label}",
                "kind": "state",
                "domain": "simulation",
                "status": "observed",
                "metrics": {"wip_evidence": round(row["wip_evidence"], 3)},
                "evidence": [],
                "notes": [],
            }
        )
        edges.append(
            {
                "id": f"e_stage_wip_{st.key}",
                "source": f"stage_{st.key}",
                "target": node_id,
                "relation": "queues_parts_into",
                "status": "observed",
                "method": "exported queue columns",
                "evidence": [],
            }
        )

    # One defect node per archive class (no join: shown as a separate domain).
    vision_classes = catalog.vision_class_counts()
    for cls in sorted(vision_classes):
        nodes.append(
            {
                "id": f"defect_{cls}",
                "label": f"Defect class: {cls}",
                "kind": "defect",
                "domain": "vision",
                "status": "observed",
                "metrics": {"images": vision_classes[cls]},
                "evidence": [],
                "notes": [
                    "The image archive shares no key with the simulation export, so this node is not "
                    "connected to the stations by data. Engineer-declared assumptions appear as assumed edges."
                ],
            }
        )

    known_classes = set(vision_classes)
    known_stations = {st.key for st in spec.stations} | {
        s.key for m in domain.MODELS.values() for s in m.stations
    }
    skipped_assumptions: List[Dict[str, str]] = []
    assumed_edges = 0
    for assumption in assumptions:
        if not assumption.get("active", True):
            continue
        cls = str(assumption.get("defect_class", "")).strip().lower()
        station = str(assumption.get("station", "")).strip().lower()
        if cls not in known_classes:
            skipped_assumptions.append(
                {
                    "defect_class": cls,
                    "station": station,
                    "reason": (
                        f"'{cls}' is not one of the archive labels "
                        f"({', '.join(sorted(known_classes))})"
                    ),
                }
            )
            continue
        if station not in known_stations:
            skipped_assumptions.append(
                {
                    "defect_class": cls,
                    "station": station,
                    "reason": (
                        f"'{station}' is not a station in the simulation exports"
                    ),
                }
            )
            continue
        edges.append(
            {
                "id": f"e_assume_{cls}_{station}",
                "source": f"defect_{cls}",
                "target": f"stage_{station}",
                "relation": "assumed_link",
                "status": "assumed",
                "method": f"engineer-declared assumption: {assumption.get('rationale', '')[:120]}",
                "evidence": [],
            }
        )
        assumed_edges += 1

    # Production outcome node. Only the Model 3 frame owns the derived parts
    # series - reading it for another dataset reported the wrong plant's output.
    parts = catalog._derived.get("model3_parts", {}).get("total_parts") if key == "model3" else None
    throughput = float(parts.mean()) if parts is not None else None
    outcome_node = {
        "id": "production_outcome",
        "label": "Production output",
        "kind": "outcome",
        "domain": "simulation",
        "status": "observed" if throughput is not None else "unavailable",
        "metrics": {"throughput_parts_per_run": round(throughput, 1)} if throughput else {},
        "evidence": [],
        "notes": [],
    }
    nodes.append(outcome_node)
    last_stage = route[-1]
    edges.append(
        {
            "id": "e_last_outcome",
            "source": f"stage_{last_stage.key}",
            "target": "production_outcome",
            "relation": "produces",
            "status": "observed",
            "method": "final documented stage feeds output",
            "evidence": [],
        }
    )

    # Economic node - gated: only present when a rate card has been applied.
    economic_node = {
        "id": "economic_impact",
        "label": "Economic impact",
        "kind": "economic",
        "domain": "derived",
        "status": "unavailable",
        "metrics": {},
        "evidence": [],
        "notes": [
            "No cost value exists in any dataset. This node becomes measurable only when an engineer "
            "supplies a rate card on the Economics view."
        ],
    }
    nodes.append(economic_node)
    edges.append(
        {
            "id": "e_outcome_economic",
            "source": "production_outcome",
            "target": "economic_impact",
            "relation": "costs",
            "status": "unavailable",
            "method": "requires a user-supplied rate card",
            "evidence": [],
        }
    )

    # Validity check: every edge endpoint must exist.
    node_ids = {n["id"] for n in nodes}
    valid_edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    dropped = len(edges) - len(valid_edges)

    return {
        "nodes": nodes,
        "edges": valid_edges,
        "assumed_edges": assumed_edges,
        "skipped_assumptions": skipped_assumptions,
        "legend": _GRAPH_LEGEND,
        "linkage_notice": catalog.linkage_report()["statement"],
        "limitations": [
            "Edges follow the documented route; no statistical causation is claimed.",
            f"{len(skipped_assumptions)} assumption(s) skipped because they named unknown classes or stations.",
            f"{dropped} edge(s) dropped because an endpoint node does not exist.",
        ]
        if skipped_assumptions or dropped
        else ["Edges follow the documented route; no statistical causation is claimed."],
    }
