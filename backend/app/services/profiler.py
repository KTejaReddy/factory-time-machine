"""Lightweight structural profiler for uploaded datasets.

Reads a dataframe and reports what it contains: column groups, the fields that
look like manufacturing concepts (stations, process variables, costs, batch
identifiers) and simple stats. It is deliberately name-based - no domain
inference beyond the column names and dtypes - because guessing a schema is how
fabricated results start. Everything it returns is observable in the file.

The output feeds :func:`app.services.capabilities.map_capabilities`, which turns
these findings into the capability map shown to the user.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import pandas as pd

#: Name fragments that identify each manufacturing concept. Lower-case
#: substring matches against the column name, mirroring how plant exports are
#: actually named (``Machine_ID``, ``temperature_C``, ``scrap_cost_usd``).
_STATION_HINTS = ("station", "machine", "cell", "press", "workcenter", "work_center", "line_id", "line_name")
_PROCESS_HINTS = (
    "util",          # utilisation / utilization
    "queue",
    "temp",
    "cycle_time",
    "cycle time",
    "pressure",
    "speed",
    "rpm",
    "flow",
    "vibration",
    "oee",
    "defect_rate",
    "scrap_rate",
    "downtime",
    "down_time",
    "throughput",
    "output",
    "wip",
    "capacity",
)
_COST_HINTS = ("cost", "price", "revenue", "spend", "expense", "rate_usd", "ratecard", "rate_card")
_BATCH_HINTS = ("batch", "lot", "run", "order", "job", "serial", "part_id", "part id")
_TIME_HINTS = ("timestamp", "datetime", "date", "time_now", "start_time", "end_time", "created_at")

_IDENTIFIER_SUFFIXES = ("id", "_id", "code", "key", "name")
_NUMERIC_NAME = re.compile(r"^-?\d+(\.\d+)?$")


def _name_matches(column: str, hints: tuple[str, ...]) -> bool:
    low = column.lower()
    return any(h in low for h in hints)


def profile_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    """Analyse a dataframe's structure and manufacturing concepts.

    All lists hold column *names* as they appear in the file so the UI can show
    the user exactly which columns were recognised (and, for capability
    reasons, how many were not).
    """
    columns: List[str] = [str(c) for c in df.columns]

    categorical: List[str] = []
    numerical: List[str] = []
    identifiers: List[str] = []
    timestamps: List[str] = []
    booleans: List[str] = []

    for col in columns:
        series = df[col]
        low = col.lower()
        if pd.api.types.is_bool_dtype(series):
            booleans.append(col)
        elif pd.api.types.is_numeric_dtype(series):
            numerical.append(col)
        elif _name_matches(col, _TIME_HINTS):
            timestamps.append(col)
        elif _name_matches(col, _BATCH_HINTS) or low.endswith(_IDENTIFIER_SUFFIXES):
            identifiers.append(col)
        else:
            categorical.append(col)

    stations = [c for c in columns if _name_matches(c, _STATION_HINTS)]
    cost_fields = [c for c in columns if _name_matches(c, _COST_HINTS)]
    process_vars = [c for c in columns if _name_matches(c, _PROCESS_HINTS)]
    batch_id_cols = [c for c in columns if _name_matches(c, _BATCH_HINTS)]

    dtypes = {c: str(df[c].dtype) for c in columns}
    numeric_summary: Dict[str, Dict[str, float]] = {}
    for col in numerical:
        series = pd.to_numeric(df[col], errors="coerce")
        if series.notna().any():
            numeric_summary[col] = {
                "min": float(series.min()),
                "max": float(series.max()),
                "mean": float(series.mean()),
            }

    return {
        "columns": columns,
        "types": {
            "categorical": categorical,
            "numerical": numerical,
            "identifiers": identifiers,
            "timestamps": timestamps,
            "booleans": booleans,
        },
        "dtypes": dtypes,
        "manufacturing": {
            "stations": stations,
            "process_vars": process_vars,
            "cost_fields": cost_fields,
            "batch_ids": len(batch_id_cols) > 0,
            "batch_id_columns": batch_id_cols,
        },
        "stats": {
            "rows": int(len(df)),
            "missing": int(df.isna().sum().sum()),
            "numeric_summary": numeric_summary,
        },
    }
