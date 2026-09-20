"""Production analytics.

Algorithms (all verified against the export, see ALGORITHM_REFERENCE.md):

* **Anomaly detection** - log1p transform, standardisation, PCA whitening, then
  the Mahalanobis distance in whitened space. The threshold is the empirical
  99.5th percentile and drivers are per-feature z-scores of each flagged row.
* **Calibration** - for each station, utilisation recomputed from the documented
  identity ``util ~= time_per_part * arrivals / (capacity * horizon)`` compared
  with the exported value.
* **Regimes / demand response** - group-by over the designed demand factor with
  per-group means, so saturation is observed rather than assumed.

Nothing here calls an LLM. Rows are independent replications: no wall-clock
ordering is claimed anywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import settings
from . import domain
from .catalog import Catalog

log = logging.getLogger("ftm.analytics")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _numeric_columns(df: pd.DataFrame, min_unique: int = 2) -> List[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique(dropna=True) >= min_unique
    ]


def model3_queue_columns(df: pd.DataFrame) -> List[str]:
    """Every exported queue column in the model-3 frame."""
    return [c for c in df.columns if c.endswith("_Queue")]


def _whiten_and_score(df: pd.DataFrame, columns: List[str]) -> tuple:
    """log1p -> standardise -> PCA-whiten -> Mahalanobis distance."""
    sub = df[columns].apply(pd.to_numeric, errors="coerce").dropna()
    if len(sub) < 50 or len(columns) < 2:
        return sub.index, np.array([]), {}
    x = np.log1p(sub.to_numpy(dtype="float64") - sub.to_numpy().min(axis=0) + 1.0)
    mean, std = x.mean(axis=0), x.std(axis=0)
    std[std == 0] = 1.0
    x = (x - mean) / std
    cov = np.cov(x, rowvar=False)
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(cov)
    diff = x - x.mean(axis=0)
    # Mahalanobis distance = sqrt(diff . inv . diff). The naive
    # ``einsum("ij,jk,ik->i")`` silently materialises an (n, k, k) intermediate -
    # 4.4 GB at 605k rows, which took 68 s and thrashed memory. A BLAS matmul
    # plus a row-wise dot is the same arithmetic in O(n*k) memory.
    projected = diff @ inv
    scores = np.sqrt(np.einsum("ij,ij->i", projected, diff))
    # The whitened matrix and the column order travel with the result so that
    # feature importance does not have to re-select 605k rows column by column
    # (that label-based lookup alone cost 32 s per report).
    return sub.index, scores, {"mean": mean, "std": std, "matrix": x, "columns": list(columns)}


# ---------------------------------------------------------------------------
# Anomaly report
# ---------------------------------------------------------------------------
def anomaly_report(catalog: Catalog, dataset_key: str = "model3", top: int = 25) -> Dict[str, Any]:
    cache_key = ("anomaly", dataset_key, top)
    cached = getattr(catalog, "analysis_cache", {}).get(cache_key)
    if cached is not None:
        return cached
    df = catalog.model_df(dataset_key)

    if dataset_key == "model3" or not dataset_key.startswith("user:"):
        # Use the documented process columns: utilisation + queue columns.
        util_cols = [c for c in df.columns if c.endswith("_Util")]
        queue_cols = model3_queue_columns(df)
        columns = [c for c in (util_cols + queue_cols) if c in df.columns]
        method_note = (
            "log1p -> standardise -> Mahalanobis on the exported utilisation and queue columns; "
            "threshold = empirical 99.5th percentile"
        )
    else:
        profile = catalog.profile_map(dataset_key)
        columns = profile.get("manufacturing", {}).get("process_vars", [])
        method_note = (
            "log1p -> standardise -> Mahalanobis on the detected numeric process columns; "
            "threshold = empirical 99.5th percentile"
        )

    if len(columns) < 2:
        return {
            "model_key": dataset_key,
            "status": "unavailable",
            "reason": (
                f"anomaly detection needs at least 2 numeric process columns; found "
                f"{len(columns)} ({', '.join(columns[:5]) or 'none'})"
            ),
            "n_rows": int(len(df)),
            "n_scored": 0,
            "threshold": 0.0,
            "anomalous_fraction": 0.0,
            "top": [],
            "feature_importance": [],
            "method": method_note,
            "limitations": ["Requires at least two process variables."],
        }

    idx, scores, score_info = _whiten_and_score(df, columns)
    if scores.size == 0:
        return {
            "model_key": dataset_key,
            "status": "unavailable",
            "reason": "not enough complete rows to score",
            "n_rows": int(len(df)),
            "n_scored": 0,
            "threshold": 0.0,
            "anomalous_fraction": 0.0,
            "top": [],
            "feature_importance": [],
            "method": method_note,
            "limitations": [],
        }

    threshold = float(np.percentile(scores, 99.5))
    flagged = np.where(scores >= threshold)[0]
    order = flagged[np.argsort(-scores[flagged])][:top]
    feature_importance = _feature_importance(score_info, scores)

    stats = catalog.population_stats(dataset_key)
    parts = (catalog._derived.get("model3_parts") or {}).get("total_parts")
    runs: List[Dict[str, Any]] = []
    for i in order:
        row = df.loc[idx[i]]
        drivers = []
        for col in columns:
            col_mean = float(stats.loc[col, "mean"]) if col in stats.index else float(row[col])
            col_std = float(stats.loc[col, "std"]) or 1.0
            z = (float(row[col]) - col_mean) / col_std
            if abs(z) > settings.divergence_z:
                # ``feature``/``direction``/``baseline_mean`` are the documented
                # names the UI reads; ``column`` is kept for callers that use the
                # raw frame name. Without the documented keys the landing page
                # crashed on ``feature.replace(...)``.
                drivers.append(
                    {
                        "feature": col,
                        "column": col,
                        "value": float(row[col]),
                        "z": round(float(z), 2),
                        "direction": "high" if z > 0 else "low",
                        "baseline_mean": col_mean,
                    }
                )
        drivers.sort(key=lambda d: -abs(d["z"]))
        run_parts = None
        if parts is not None:
            try:
                got = parts.get(idx[i])
                run_parts = None if got is None else int(got)
            except Exception:  # a non-model3 frame has no derived counter
                run_parts = None
        runs.append(
            {
                "run_index": int(idx[i]),
                "score": round(float(scores[i]), 3),
                "parts": run_parts,
                "drivers": drivers[:4],
            }
        )

    report = {
        "model_key": dataset_key,
        "status": "success",
        "n_rows": int(len(df)),
        "n_scored": int(scores.size),
        "threshold": round(threshold, 3),
        "anomalous_fraction": float((scores >= threshold).mean()),
        "anomalous_runs": int(flagged.size),
        "top": runs,
        "feature_importance": feature_importance,
        "method": method_note,
        "limitations": [
            "Rows are independent replications: an outlier replication is not a moment in time.",
            "Scores are relative to this dataset's own distribution, not an absolute defect scale.",
        ],
    }
    if hasattr(catalog, "analysis_cache"):
        catalog.analysis_cache[cache_key] = report
    return report


def _feature_importance(score_info: Dict[str, Any], scores: np.ndarray) -> List[Dict[str, Any]]:
    """How much each column contributes to the spread of the Mahalanobis score.

    Uses the whitened matrix already computed for the scores: the previous
    implementation re-selected every column from the full frame by label, which
    cost ~32 s on the 605k-row export.
    """
    x = (score_info or {}).get("matrix")
    columns = (score_info or {}).get("columns") or []
    if x is None or scores is None or len(scores) == 0:
        return []
    out = []
    for j, col in enumerate(columns):
        column = np.asarray(x[:, j], dtype="float64")
        if column.std() == 0:
            continue
        corr = float(np.corrcoef(column, scores)[0, 1])
        # ``feature``/``loading`` are the documented names; the column-named
        # duplicates keep the raw frame name available to callers.
        out.append(
            {
                "feature": col,
                "column": col,
                "loading": round(corr, 3),
                "correlation_with_score": round(corr, 3),
            }
        )
    out.sort(key=lambda d: -abs(d["correlation_with_score"]))
    return out[:10]


# ---------------------------------------------------------------------------
# Calibration (utilisation identity vs exported values)
# ---------------------------------------------------------------------------
def calibration_report(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    df = catalog.model_df(key)
    stats = catalog.population_stats(key)
    spec = domain.model_for(key, catalog=catalog)
    if spec is None:
        return {"model_key": key, "stations": [], "note": "no documented model for this dataset"}

    horizon = settings.sim_horizon_seconds
    stations: List[Dict[str, Any]] = []
    for st in spec.stations:
        col = st.utilization_column
        if not col or col not in df.columns:
            continue
        exported = float(stats.loc[col, "mean"])
        queue_cols = [c for c in st.queue_columns if c in df.columns]
        station_out = {
            "station": st.key,
            "label": st.label,
            "exported_utilization": exported,
            "capacity": st.capacity,
        }
        if st.processing_time_seconds and st.capacity:
            # Identity: util = time_per_part * arrivals / (capacity * horizon).
            # Arrivals are approximated from the plant throughput.
            parts = catalog._derived.get("model3_parts", {}).get("total_parts")
            if parts is not None:
                arrivals = float(parts.mean())
                predicted = st.processing_time_seconds * arrivals / (st.capacity * horizon)
                station_out["identity_predicted_utilization"] = round(predicted, 4)
                station_out["residual"] = round(exported - predicted, 4)
        stations.append(station_out)
    return {
        "model_key": key,
        "stations": stations,
        "note": (
            "Utilisation identity check against the export. Residuals show where the documented "
            "capacity/processing time and the export disagree (see the Cell4 contradiction)."
        ),
    }


# ---------------------------------------------------------------------------
# Regimes (group contrasts)
# ---------------------------------------------------------------------------
def regime_contrast(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    """Contrast the lowest- and highest-output replication groups."""
    df = catalog.model_df(key)
    parts = catalog._derived.get("model3_parts", {}).get("total_parts")
    if parts is None:
        return {"model_key": key, "status": "unavailable", "reason": "no throughput column derived"}

    q1, q99 = parts.quantile(0.01), parts.quantile(0.99)
    low_mask, high_mask = parts <= q1, parts >= q99
    util_cols = [c for c in df.columns if c.endswith("_Util")]
    rows = []
    for label, mask in (("lowest-output 1%", low_mask), ("highest-output 1%", high_mask)):
        entry: Dict[str, Any] = {"group": label, "n": int(mask.sum())}
        for col in util_cols[:12]:
            entry[col] = round(float(df.loc[mask, col].mean()), 4)
        rows.append(entry)
    return {
        "model_key": key,
        "status": "success",
        "groups": rows,
        "note": "Utilisation means across the extreme-output replication groups.",
    }


# ---------------------------------------------------------------------------
# Demand response (designed factor levels)
# ---------------------------------------------------------------------------
def demand_response(catalog: Catalog, key: str = "model1") -> Dict[str, Any]:
    """Throughput and utilisation per designed demand level (Model 1 and 2)."""
    df = catalog.model_df(key)
    if "Demand" not in df.columns:
        return {
            "model_key": key,
            "status": "unavailable",
            "reason": "this dataset has no designed demand factor column",
        }
    demand = df["Demand"].astype(int)
    # ``int(level)``: numpy scalars are not JSON serialisable, which turned this
    # successful computation into a 500 for Models 1 and 2.
    levels = [int(level) for level in sorted(demand.unique())]
    util_cols = [c for c in df.columns if "util" in c.lower()]
    points: List[Dict[str, Any]] = []
    for level in levels:
        sub = df[demand == level]
        point: Dict[str, Any] = {"demand": int(level), "runs": int(len(sub))}
        throughput_col = next(
            (c for c in ("Parts per hour", "Entities Out", "Total parts") if c in sub.columns), None
        )
        point["parts_per_hour"] = (
            round(float(sub[throughput_col].mean()), 2) if throughput_col else 0.0
        )
        point["utilisation"] = {c: round(float(sub[c].mean()), 4) for c in util_cols}
        wait_cols = [c for c in sub.columns if "wait" in c.lower() or "queue" in c.lower()]
        point["waiting_time"] = {c: round(float(sub[c].mean()), 2) for c in wait_cols}
        points.append(point)

    saturation = _find_saturation(points)
    return {
        "model_key": key,
        "factor": "Demand",
        "factor_levels": [int(level) for level in levels],
        "metric_label": "parts per hour" if "Parts per hour" in df.columns else "entities out",
        "points": points,
        "saturation": saturation,
        "surrogate": _surrogate_note(),
        "note": "Observed response to the designed demand levels, from 150 runs per level.",
    }


def _find_saturation(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The level beyond which throughput stops rising with demand."""
    throughputs = [p["parts_per_hour"] for p in points]
    demands = [p["demand"] for p in points]
    for i in range(1, len(throughputs)):
        if throughputs[i] <= throughputs[i - 1] * 1.01:
            return {
                "demand_level": demands[i],
                "throughput": throughputs[i],
                "definition": "first demand level where throughput no longer increases by more than 1%",
            }
    return {"demand_level": None, "definition": "no saturation observed within the designed levels"}


def _surrogate_note() -> Dict[str, Any]:
    return {
        "note": "The MATLAB surrogate design (3000Samplesv3.mat) is a separate experiment; see /api/datasets/design.",
    }


# ---------------------------------------------------------------------------
# Stage association (which columns move together)
# ---------------------------------------------------------------------------
def stage_association(catalog: Catalog, key: str = "model3") -> Dict[str, Any]:
    df = catalog.model_df(key)
    parts = catalog._derived.get("model3_parts", {}).get("total_parts")
    if parts is None:
        return {"model_key": key, "status": "unavailable", "reason": "no throughput column derived"}

    util_cols = [c for c in df.columns if c.endswith("_Util")]
    queue_cols = model3_queue_columns(df)
    columns = util_cols + queue_cols
    if not columns:
        return {"model_key": key, "status": "unavailable", "reason": "no utilisation or queue columns"}

    sub = df[columns].apply(pd.to_numeric, errors="coerce").dropna()
    if sub.empty:
        return {"model_key": key, "status": "unavailable", "reason": "no complete rows across the station columns"}
    # Constant columns have no correlation (and made numpy print a divide
    # warning); drop them before computing anything.
    sub = sub.loc[:, sub.std() > 0]
    # Series.sort_values has no ``reverse`` (this raised TypeError and returned a
    # 500); order by |correlation| explicitly.
    corr = sub.corrwith(parts.loc[sub.index]).dropna()
    corr = corr.reindex(corr.abs().sort_values(ascending=False).index)
    associations = [
        {"column": col, "correlation_with_output": round(float(corr[col]), 3)}
        for col in corr.index
        if abs(corr[col]) >= 0.05
    ]
    return {
        "model_key": key,
        "status": "success",
        "associations": associations,
        "note": (
            "Pearson correlation between each station's exported columns and total assembled parts. "
            "Correlation is not causation; these are leads for investigation, never verdicts."
        ),
    }
