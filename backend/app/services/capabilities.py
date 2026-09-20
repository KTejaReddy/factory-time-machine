"""Capability map for user-uploaded datasets.

The catalog answers "what can this dataset support?" for the built-in archives
with ``{"available": {...}, "reasons": {...}}``. Uploaded CSVs go through the
profiler and get the same shape from here, so the UI renders every dataset with
one code path and every switched-off capability carries the reason it is off.

The flags describe what the file itself contains. They never promise that the
deep analyses calibrated to the supplied Model 3 export (what-if simulation,
bottleneck scoring) will run on an arbitrary schema - the ``simulation`` flag
stays off for uploads and says so.
"""

from __future__ import annotations

from typing import Any, Dict

FEATURES = ("vision", "production", "anomaly", "forensics", "economics", "simulation")


def map_capabilities(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a profiler report into the catalog's capability shape.

    ``profile`` is the output of :func:`app.services.profiler.profile_dataframe`.
    Missing sections degrade to "not detected" rather than raising, because an
    upload that is missing columns is a normal case, not an error.
    """
    m = profile.get("manufacturing", {}) or {}
    cost_fields = list(m.get("cost_fields", []) or [])
    station_fields = list(m.get("stations", []) or [])
    process_fields = list(m.get("process_vars", []) or [])
    batch_ids = bool(m.get("batch_ids", False))

    has_cost = len(cost_fields) > 0
    has_stations = len(station_fields) > 0
    # A PCA/Mahalanobis space needs at least two numeric process columns; one
    # column cannot carry a covariance.
    has_process = len(process_fields) >= 2

    available: Dict[str, bool] = {
        "vision": False,
        "production": has_process,
        "anomaly": has_process,
        "forensics": has_stations and has_process,
        "economics": has_cost,
        "simulation": False,
    }
    reasons: Dict[str, str] = {
        "vision": (
            "a CSV upload contains no images. The defect model works on the supplied "
            "image archive and on images you upload on the Inspect page."
        ),
        "simulation": (
            "the what-if engine is calibrated to the supplied Model 3 export; an uploaded "
            "CSV is profiled but not simulated."
        ),
    }
    if not has_process:
        reasons["production"] = (
            "no numeric process columns (utilisation, queue, temperature, cycle time, ...) "
            f"were detected; found {len(process_fields)}"
        )
        reasons["anomaly"] = (
            "anomaly detection needs at least two numeric process columns to build a PCA "
            f"space; found {len(process_fields)}"
        )
    if not has_stations:
        reasons["forensics"] = "no station or machine column was detected"
    elif not has_process:
        reasons["forensics"] = (
            "a station column was found, but ordering a route also needs the numeric "
            "process columns that were not detected"
        )
    if not has_cost:
        reasons["economics"] = (
            "no cost, price or revenue columns were detected. Economic analysis needs a "
            "rate card; add cost columns or supply one on the What-If page."
        )

    return {
        "available": available,
        "reasons": reasons,
        "detected": {
            "station_fields": station_fields,
            "process_fields": process_fields,
            "cost_fields": cost_fields,
            "batch_ids": batch_ids,
        },
    }
