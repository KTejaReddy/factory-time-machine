"""Economic impact.

The rule this module enforces
-----------------------------
**No monetary value is ever produced from the dataset**, because no dataset in
this project contains one. A scan of the Arena files shows the industrial models
*do* carry costing constructs (``V_VACost``, ``V_NVACost``, ``V_TranCost``,
``V_WaitCost``, ``Holding Cost / Hour``, ``Costing_Use``), but none of those
values is exported in any CSV, and no currency, price, scrap count or downtime
figure exists anywhere in the data.

So the API returns ``available: false`` with the precise reason, and the only way
to get a number is for an engineer to supply a rate card. When they do, the
*quantities* still come from the dataset and every line records which side it came
from, so a reader can never mistake a user-supplied rate for measured data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..config import settings
from ..schemas import CostRateCard
from . import domain
from .catalog import Catalog

log = logging.getLogger("ftm.economics")

DISCLAIMER = (
    "Quantities come from the supplied dataset; every rate is entered by the user and is not part of any "
    "dataset. Figures are advisory and are not accounting values."
)

MISSING_VARIABLES = [
    "unit price or margin per product",
    "scrap count and scrap cost",
    "rework count and rework cost",
    "downtime hours",
    "holding cost per unit per hour",
    "currency and cost-accounting basis",
]

#: What the Arena files reference but never export (found by scanning the .doe files).
ARENA_COSTING_NOT_EXPORTED = [
    "Costing_Use",
    "V_VACost",
    "V_NVACost",
    "V_TranCost",
    "V_WaitCost",
    "V_OtherCost",
    "InitVACost",
    "InitNVACost",
    "InitTranCost",
    "Holding Cost / Hour",
    "Cost to Duplicates",
]


def _rates_provided(rate_card: Optional[CostRateCard]) -> bool:
    if rate_card is None:
        return False
    return any(
        getattr(rate_card, field) is not None
        for field in (
            "margin_per_unit",
            "cost_per_unit_scrapped",
            "holding_cost_per_unit_hour",
            "downtime_cost_per_hour",
            "rework_cost_per_unit",
        )
    )


#: Money concepts an uploaded dataset can carry itself. The value attached to
#: each is the mean of a column whose name states what it is - never inferred.
_DATASET_COST_FIELDS = {
    "scrap": ("scrap_cost", "scrap_cost_per_unit"),
    "rework": ("rework_cost", "rework_cost_per_unit"),
    "downtime": ("downtime_cost", "downtime_cost_per_hour"),
    "holding": ("holding_cost",),
    "unit_value": ("margin", "margin_per_unit", "unit_price", "price_per_unit", "revenue", "unit_value"),
}


def _dataset_cost_rates(df) -> Dict[str, Dict[str, Any]]:
    """Cost/price columns the dataset itself carries (case A of the cost spec)."""
    out: Dict[str, Dict[str, Any]] = {}
    columns = [str(c) for c in df.columns]
    for concept, needles in _DATASET_COST_FIELDS.items():
        for col in columns:
            low = col.lower()
            if any(n in low for n in needles):
                try:
                    value = float(np.nanmean(np.asarray(df[col], dtype="float64")))
                except Exception:  # pragma: no cover - non-numeric column
                    continue
                if np.isfinite(value):
                    out[concept] = {"value": value, "column": col}
                break
    return out


def _dataset_currency(df) -> Optional[str]:
    for col in df.columns:
        if "currency" in str(col).lower():
            try:
                values = df[col].dropna().astype(str).unique().tolist()
                if values:
                    return str(values[0])[:12]
            except Exception:  # pragma: no cover - defensive
                return None
    return None


def _line(
    label: str,
    quantity: Optional[float],
    unit: str,
    rate: Optional[float],
    amount: Optional[float],
    quantity_source: str,
    note: str = "",
    rate_source: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "label": label,
        "quantity": quantity,
        "unit": unit,
        "rate": rate,
        "amount": amount,
        "quantity_source": quantity_source,
        "rate_source": rate_source or ("user-supplied" if rate is not None else "not supplied"),
        "note": note,
    }


def assess(catalog: Catalog, key: str = "model3", rate_card: Optional[CostRateCard] = None) -> Dict[str, Any]:
    """Economic assessment of the current dataset state.

    Two supported cases, kept distinct in the output:

    * **the dataset carries cost/price columns** (an uploaded CSV can): those values
      are used, labelled with the column they came from;
    * **it does not**: the user's rate card supplies the rates and the dataset
      supplies the quantities.
    """
    df = catalog.model_df(key)
    dataset_rates = _dataset_cost_rates(df)
    dataset_currency = _dataset_currency(df)
    provided = _rates_provided(rate_card) or bool(dataset_rates)
    available_quantities = _available_quantities(catalog, key)

    def rate_of(field: str, concept: str) -> tuple:
        """(value, source). The user's stated rate wins; the dataset's own column is the fallback."""
        value = getattr(rate_card, field, None) if rate_card else None
        if value is not None:
            return float(value), f"user-supplied rate card ({field})"
        entry = dataset_rates.get(concept)
        if entry and entry.get("value") is not None:
            return float(entry["value"]), f"dataset column '{entry['column']}' (mean)"
        return None, "not supplied"

    if not provided:
        return {
            "available": False,
            "reason": (
                "Economic impact cannot be calculated from the current dataset. It contains no cost, price or "
                "revenue column, and no model exports one, so there is nothing to multiply by a quantity. "
                "Supply a rate card to compute an advisory figure from the measured quantities."
            ),
            "currency": (rate_card.currency if rate_card else None) or dataset_currency,
            "lines": [],
            "total": None,
            "missing_variables": MISSING_VARIABLES,
            "dataset_supplied_values": available_quantities["labels"],
            "arena_costing_not_exported": ARENA_COSTING_NOT_EXPORTED,
            "disclaimer": DISCLAIMER,
        }

    hours = settings.sim_horizon_seconds / 3600.0
    lines: List[Dict[str, Any]] = []

    holding_rate, holding_source = rate_of("holding_cost_per_unit_hour", "holding")
    wip = available_quantities.get("wip_parts")
    if holding_rate is not None and wip is not None:
        amount = wip * holding_rate * hours
        lines.append(
            _line(
                "Work-in-process holding cost",
                wip,
                "parts (mean queue/WIP)",
                holding_rate,
                amount,
                available_quantities["wip_source"],
                f"applied over the {hours:.1f} h horizon",
                rate_source=holding_source,
            )
        )

    value_rate, value_source = rate_of("margin_per_unit", "unit_value")
    spread = available_quantities.get("output_spread")
    if value_rate is not None and spread is not None:
        lines.append(
            _line(
                "Unrealised output vs the best measured replication",
                spread["quantity"],
                "parts between the population mean and the 99th percentile",
                value_rate,
                spread["quantity"] * value_rate,
                spread["source"],
                "measures the output spread observed across independent replications, not a proven loss event",
                rate_source=value_source,
            )
        )

    # Scrap / rework / downtime: evaluated when the dataset measures the quantity
    # (an uploaded CSV can), otherwise reported as unevaluated rather than as zero.
    scrap_rate, scrap_rate_source = rate_of("cost_per_unit_scrapped", "scrap")
    scrap_qty = available_quantities.get("scrap_units")
    if scrap_rate is not None:
        evaluated = scrap_qty is not None
        lines.append(
            _line(
                "Scrap",
                scrap_qty,
                "units (mean per row)" if evaluated else "units",
                scrap_rate,
                scrap_qty * scrap_rate if evaluated else None,
                available_quantities["scrap_source"] if evaluated else "not available",
                (
                    "the dataset measures a scrap/defect count; the rate is stated in the line's rate_source"
                    if evaluated
                    else (
                        "Model 3.doe contains scrap logic but no scrap count is exported, so this line cannot be "
                        "evaluated"
                    )
                ),
                rate_source=scrap_rate_source,
            )
        )

    rework_rate, rework_rate_source = rate_of("rework_cost_per_unit", "rework")
    rework_qty = available_quantities.get("rework_units")
    if rework_rate is not None:
        evaluated = rework_qty is not None
        lines.append(
            _line(
                "Rework",
                rework_qty,
                "units (mean per row)" if evaluated else "units",
                rework_rate,
                rework_qty * rework_rate if evaluated else None,
                available_quantities["rework_source"] if evaluated else "not available",
                (
                    "the dataset measures a rework count; the rate is stated in the line's rate_source"
                    if evaluated
                    else "no export contains a rework count, so this line cannot be evaluated"
                ),
                rate_source=rework_rate_source,
            )
        )

    downtime_rate, downtime_rate_source = rate_of("downtime_cost_per_hour", "downtime")
    downtime_qty = available_quantities.get("downtime_hours")
    if downtime_rate is not None:
        evaluated = downtime_qty is not None
        lines.append(
            _line(
                "Downtime",
                downtime_qty,
                "hours (mean per row)" if evaluated else "hours",
                downtime_rate,
                downtime_qty * downtime_rate if evaluated else None,
                available_quantities["downtime_source"] if evaluated else "not available",
                (
                    "the dataset measures a duration and its column name states the unit"
                    if evaluated
                    else "no export contains a downtime column, so this line cannot be evaluated"
                ),
                rate_source=downtime_rate_source,
            )
        )

    valued = [l for l in lines if l["amount"] is not None]
    effective_rates = {
        "holding": holding_rate,
        "margin": value_rate,
        "scrap": scrap_rate,
        "rework": rework_rate,
        "downtime": downtime_rate,
    }
    total = sum(l["amount"] for l in valued)
    return {
        "available": bool(valued),
        "reason": (
            "computed from this dataset's measured quantities and the rates stated in each line's rate_source"
            if valued
            else "the available rates do not match any quantity that exists in this dataset"
        ),
        "currency": (rate_card.currency if rate_card else None) or dataset_currency,
        "lines": lines,
        "total": float(total) if valued else None,
        "missing_variables": [v for v in MISSING_VARIABLES if not _addressed(v, effective_rates)],
        "dataset_supplied_values": available_quantities["labels"],
        "arena_costing_not_exported": ARENA_COSTING_NOT_EXPORTED,
        "disclaimer": DISCLAIMER,
    }


def _addressed(variable: str, rates: Dict[str, Optional[float]]) -> bool:
    if "holding cost" in variable:
        return rates.get("holding") is not None
    if "margin" in variable:
        return rates.get("margin") is not None
    if "rework" in variable:
        return rates.get("rework") is not None
    if "scrap" in variable:
        return rates.get("scrap") is not None
    if "downtime" in variable:
        return rates.get("downtime") is not None
    return False


def _available_quantities(catalog: Catalog, key: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"labels": []}
    try:
        if key == "model3":
            # Only the Model 3 frame owns this derived series; using it for another
            # dataset would value the wrong rows.
            parts = catalog._derived.get("model3_parts", {}).get("total_parts")
            sample = catalog.sample_df("model3")
            if parts is not None:
                out["throughput_parts"] = float(parts.mean())
                out["labels"].append("assembled parts per run (sum of exported cell counters)")
                out["output_spread"] = {
                    "quantity": float(np.nanquantile(parts, 0.99) - parts.mean()),
                    "source": "sum of exported c_CellX__SKUY counters across 605,620 replications",
                }
            queue_cols = [
                c
                for station in domain.MODELS["model3"].stations
                for c in station.queue_columns
                if c in sample.columns
            ]
            if queue_cols:
                wip = float(np.mean([float(sample[c].mean()) for c in queue_cols]))
                out["wip_parts"] = wip
                out["wip_source"] = "exported queue columns (the export has no dedicated WIP column)"
                out["labels"].append("queue lengths / WIP (exported queue columns)")
            wait_cols = [c for c in sample.columns if c.startswith("SKU") and c.endswith("_Wait_Time")]
            if wait_cols:
                out["wait_time_seconds"] = float(np.mean([float(sample[c].mean()) for c in wait_cols]))
                out["labels"].append("per-SKU waiting time (exported time-breakdown columns)")
        else:
            spec = domain.model_for(key, catalog=catalog)
            df = catalog.model_df(key)
            for column in spec.throughput_columns:
                if column in df.columns:
                    out["labels"].append(f"{column} (exported throughput column)")
                    out["throughput_parts"] = float(catalog.population_stats(key).loc[column, "mean"])
                    break
            wip_cols = [c for c in spec.wip_columns if c in df.columns]
            if wip_cols:
                out["wip_parts"] = float(np.mean([float(catalog.population_stats(key).loc[c, "mean"]) for c in wip_cols]))
                out["wip_source"] = "exported stored-part counters"
                out["labels"].append("stored parts (exported WIP counters)")
            # Uploaded CSVs have no documented model: their quantities come from
            # the columns the profiler detected. Nothing is inferred beyond the name.
            _user_quantities(catalog, key, df, out)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("could not derive economic quantities: %s", exc)
    return out


def _user_quantities(catalog: Catalog, key: str, df, out: Dict[str, Any]) -> None:
    """Measured quantities in an uploaded CSV, named exactly as its columns are."""

    def mean(col: str) -> Optional[float]:
        try:
            series = np.asarray(df[col], dtype="float64")
            return float(np.nanmean(series)) if series.size else None
        except Exception:  # pragma: no cover - defensive
            return None

    def find(*needles: str, exclude: tuple = ()) -> Optional[str]:
        for col in df.columns:
            low = str(col).lower()
            if any(n in low for n in needles) and not any(e in low for e in exclude):
                return str(col)
        return None

    scrap_col = find("scrap", exclude=("cost", "rate", "price"))
    if scrap_col:
        value = mean(scrap_col)
        if value is not None:
            out["scrap_units"] = value
            out["scrap_source"] = f"dataset column '{scrap_col}' (mean per row)"
            out["labels"].append(f"scrap units ({scrap_col})")
            deviation = "defect" if "defect" in scrap_col.lower() else "scrap"
            if not any(deviation in l.lower() for l in out["labels"]):
                out["labels"].append(f"{deviation} units ({scrap_col})")

    defect_col = find("defect")
    if defect_col and not out.get("scrap_units"):
        value = mean(defect_col)
        if value is not None:
            out["scrap_units"] = value
            out["scrap_source"] = f"dataset column '{defect_col}' (mean per row)"
            out["labels"].append(f"defect units ({defect_col})")

    rework_col = find("rework", exclude=("cost", "rate", "price"))
    if rework_col:
        value = mean(rework_col)
        if value is not None:
            out["rework_units"] = value
            out["rework_source"] = f"dataset column '{rework_col}' (mean per row)"
            out["labels"].append(f"rework units ({rework_col})")

    downtime_col = find("downtime", "down_time")
    if downtime_col:
        value = mean(downtime_col)
        if value is not None:
            low = downtime_col.lower()
            hours = None
            if "_h" in low or "hour" in low or low.endswith("hr") or low.endswith("hrs"):
                hours = value
            elif "_min" in low or "minute" in low:
                hours = value / 60.0
            elif low.endswith("_s") or "second" in low:
                hours = value / 3600.0
            if hours is not None:
                out["downtime_hours"] = hours
                out["downtime_source"] = (
                    f"dataset column '{downtime_col}' (mean per row, converted to hours from the unit in its name)"
                )
                out["labels"].append(f"downtime hours ({downtime_col})")

    queue_col = find("queue", "wip")
    if queue_col and not out.get("wip_parts"):
        value = mean(queue_col)
        if value is not None:
            out["wip_parts"] = value
            out["wip_source"] = f"dataset column '{queue_col}' (mean per row)"
            out["labels"].append(f"queue/WIP ({queue_col})")


def economics_for_scenario(
    catalog: Catalog,
    payload: Dict[str, Any],
    rate_card: Optional[CostRateCard],
    key: str = "model3",
) -> Dict[str, Any]:
    """Valuation of a what-if delta, again gated on user-supplied rates."""
    base = assess(catalog, key, rate_card)
    summary = payload.get("summary", {})
    if not _rates_provided(rate_card) or summary.get("completed_parts") is None:
        return {
            **base,
            "reason": base["reason"]
            if not _rates_provided(rate_card)
            else "the scenario did not report completed parts, so no monetary delta was computed",
        }

    hours = settings.sim_horizon_seconds / 3600.0
    lines = list(base["lines"])
    baseline_completed = summary.get("baseline_released_parts")
    simulated_completed = summary.get("completed_parts")
    if (
        rate_card
        and rate_card.margin_per_unit is not None
        and baseline_completed is not None
        and simulated_completed is not None
    ):
        delta = float(simulated_completed) - float(baseline_completed)
        lines.append(
            _line(
                "Scenario output delta",
                delta,
                "parts completed within the horizon",
                rate_card.margin_per_unit,
                delta * rate_card.margin_per_unit,
                "simulated by the discrete-event engine, calibrated to the measured production volume",
                "a positive value means the scenario completes more parts within the same horizon",
            )
        )
    wip = summary.get("wip_parts_end_of_horizon")
    if rate_card and rate_card.holding_cost_per_unit_hour is not None and wip is not None:
        lines.append(
            _line(
                "Scenario end-of-horizon WIP holding cost",
                wip,
                "parts",
                rate_card.holding_cost_per_unit_hour,
                wip * rate_card.holding_cost_per_unit_hour * hours,
                "simulated end-of-horizon work in process",
                f"applied over the {hours:.1f} h horizon",
            )
        )
    totals = [l["amount"] for l in lines if l["amount"] is not None]
    return {
        "available": bool(totals),
        "reason": "scenario delta valued with user-supplied rates",
        "currency": rate_card.currency if rate_card else None,
        "lines": lines,
        "total": float(sum(totals)) if totals else None,
        "missing_variables": [v for v in MISSING_VARIABLES if not _addressed(v, rate_card)],
        "dataset_supplied_values": base["dataset_supplied_values"],
        "arena_costing_not_exported": ARENA_COSTING_NOT_EXPORTED,
        "disclaimer": DISCLAIMER,
    }
