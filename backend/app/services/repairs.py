"""Repair / intervention engine.

Answers two questions from evidence only:

1. **What can this dataset's user actually change?** Candidate levers are
   discovered from the dataset itself (the documented route model when it applies,
   otherwise the numeric process and cost columns the profiler detected). There is
   no fixed universal repair list.
2. **Which supported repair is the most economically efficient?** Each candidate
   carries an expected loss reduction, an intervention cost, a net impact, a
   confidence and its assumptions.

Rules that keep the numbers honest:

* every monetary rate is either **measured** from a dataset cost column or
  **supplied by the user** in the rate card - never guessed;
* the intervention cost is never present in the data, so it can only come from the
  rate card; a candidate without one keeps ``intervention_cost.amount = None`` and
  cannot be ranked as "cheapest";
* when no cost information at all is available the engine says so in one sentence
  instead of producing a ranking.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import settings
from ..schemas import CostRateCard, ScenarioSpec
from . import simulation
from .catalog import Catalog

log = logging.getLogger("ftm.repairs")

#: How much of the measured quantity a repair is assumed to remove. Assumptions,
#: not measurements - they are returned with every candidate.
DEFAULT_REDUCTIONS = {
    "downtime": 0.20,
    "scrap": 0.10,
    "rework": 0.10,
    "queue": 0.20,
    "cycle_time": 0.10,
}

#: A lever is only offered when its cost/benefit can be traced to these.
_COST_FIELDS = {
    "scrap": ("scrap_cost", "scrap_cost_per_unit", "cost_per_unit_scrapped"),
    "rework": ("rework_cost", "rework_cost_per_unit"),
    "downtime": ("downtime_cost", "downtime_cost_per_hour"),
}
_VALUE_FIELDS = ("margin", "margin_per_unit", "unit_price", "price_per_unit", "revenue", "unit_value")


def _numeric(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _find(columns: List[str], *needles: str, exclude: tuple = ()) -> Optional[str]:
    for col in columns:
        low = str(col).lower()
        if any(n in low for n in needles) and not any(e in low for e in exclude):
            return col
    return None


def _mean(df: pd.DataFrame, column: str) -> Optional[float]:
    try:
        value = pd.to_numeric(df[column], errors="coerce").mean()
        return float(value) if pd.notna(value) else None
    except Exception:  # pragma: no cover - defensive
        return None


def _hours_from_column(df: pd.DataFrame, column: str) -> Optional[float]:
    """Mean of a duration column, converted to hours when the name states the unit.

    A column whose unit is not stated in its name is *not* silently assumed to be
    hours: the candidate then reports the reason instead of a number.
    """
    low = column.lower()
    mean = _mean(df, column)
    if mean is None:
        return None
    if "_h" in low or "hour" in low or low.endswith("_hrs") or low.endswith("_hr"):
        return mean
    if low.endswith("_min") or "minute" in low:
        return mean / 60.0
    if low.endswith("_s") or low.endswith("_sec") or "second" in low:
        return mean / 3600.0
    return None


def _dataset_costs(df: pd.DataFrame, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Cost/value rates detected *inside* the dataset (case A).

    Only columns whose names state what they are get used, and the value is the
    column's mean, i.e. always traceable to the file.
    """
    columns = [str(c) for c in df.columns]
    numeric = set(_numeric(df))
    found: Dict[str, Any] = {}
    for concept, needles in _COST_FIELDS.items():
        col = _find(columns, *needles)
        if col and col in numeric:
            found[concept] = {"column": col, "mean": _mean(df, col)}
    value_col = _find(columns, *_VALUE_FIELDS, exclude=("cost", "total_cost"))
    if value_col and value_col in numeric:
        found["unit_value"] = {"column": value_col, "mean": _mean(df, value_col)}
    return found


def _intervention_cost(kind: str, rate_card: Optional[CostRateCard]) -> Dict[str, Any]:
    """One-off intervention cost: rate-card-only, because no dataset exports it."""
    if rate_card is None:
        return {
            "amount": None,
            "reason": "no rate card: an intervention cost is an engineering estimate that is not part of any dataset",
        }
    if kind == "capacity":
        amount = rate_card.intervention_cost_per_capacity_unit
        if amount is not None:
            return {"amount": float(amount), "basis": "rate card: cost of one additional parallel resource"}
    if kind in {"processing_time", "queue", "scrap", "rework", "downtime"}:
        amount = rate_card.intervention_cost_fixed
        if amount is not None:
            return {"amount": float(amount), "basis": "rate card: one-off engineering/process change cost"}
    return {
        "amount": None,
        "reason": (
            "the rate card does not contain an intervention cost for this kind of change "
            f"({kind}). Add a one-off cost to compare repairs economically."
        ),
    }


def _value_rate(context: Dict[str, Any], concept: str) -> Optional[Dict[str, Any]]:
    """Rate for one money concept, from the dataset or the user's rate card."""
    rates = context["rates"]
    return rates.get(concept)


def _candidate(
    *,
    repair: str,
    kind: str,
    target: Dict[str, Any],
    measured: Dict[str, Any],
    loss_reduction: Dict[str, Any],
    intervention: Dict[str, Any],
    throughput_effect: Dict[str, Any],
    quality_effect: str,
    evidence: List[str],
    assumptions: List[str],
    confidence: str,
    scenario: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    amount = loss_reduction.get("amount")
    cost = intervention.get("amount")
    net = None
    if amount is not None:
        net = round(float(amount) - (float(cost) if cost is not None else 0.0), 2)
        # A repair with no cost information has no net impact yet: subtracting a
        # missing cost would silently pretend it is free.
        if cost is None:
            net = None
    return {
        "repair": repair,
        "kind": kind,
        "target": target,
        "measured": measured,
        "expected_quality_effect": quality_effect,
        "expected_throughput_effect": throughput_effect,
        "expected_loss_reduction": loss_reduction,
        "intervention_cost": intervention,
        "net_impact": {"amount": net, "currency": loss_reduction.get("currency")},
        "confidence": confidence,
        "evidence": evidence,
        "assumptions": assumptions,
        "scenario": scenario,
    }


def _loss_from_quantity(
    *,
    label: str,
    quantity: Optional[float],
    unit: str,
    rate: Optional[Dict[str, Any]],
    reduction: float,
    context: Dict[str, Any],
    notes: List[str],
    observations: int = 1,
) -> Dict[str, Any]:
    """Value a 10-20% cut in a measured quantity.

    The quantity is a per-row mean, so the dataset total scales with the row count.
    Both figures are returned: the total is what a planning decision needs, and the
    per-row figure is what to look at when the rows are independent samples rather
    than successive periods of one plant. Which reading applies is stated as an
    assumption, never hidden.
    """
    if quantity is None:
        return {"amount": None, "currency": context["currency"], "lines": [], "reason": "the dataset has no measurable quantity for this lever"}
    if rate is None or rate.get("value") is None:
        return {
            "amount": None,
            "currency": context["currency"],
            "lines": [],
            "reason": f"no {label.lower()} rate is available (not in the dataset, not in the rate card)",
        }
    per_row = float(quantity) * float(rate["value"]) * reduction
    total = per_row * max(1, int(observations))
    return {
        "amount": round(total, 2),
        "per_row_amount": round(per_row, 2),
        "observations": max(1, int(observations)),
        "currency": context["currency"],
        "lines": [
            {
                "label": label,
                "quantity": float(quantity),
                "unit": unit,
                "rate": float(rate["value"]),
                "rate_source": rate["source"],
                "amount": round(per_row, 2),
                "note": f"a {reduction * 100:.0f}% reduction of the measured quantity, per row",
            },
            {
                "label": f"{label} (whole dataset)",
                "quantity": max(1, int(observations)),
                "unit": "rows treated as observation periods",
                "rate": round(per_row, 2),
                "rate_source": "the per-row figure above",
                "amount": round(total, 2),
                "note": "scaled over every row in the dataset",
            },
        ],
        "notes": notes,
    }


def _sim_effect(baseline: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "baseline_completed_parts": baseline.get("completed_parts"),
        "scenario_completed_parts": scenario.get("completed_parts"),
        "completed_parts_delta": (scenario.get("completed_parts") or 0) - (baseline.get("completed_parts") or 0),
        "released_parts_delta": (scenario.get("released_parts") or 0) - (baseline.get("released_parts") or 0),
        "engine": "discrete_event_resimulation",
    }


def _sim_repairs(catalog: Catalog, key: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Levers the re-simulation engine can actually measure (documented Model 3 only)."""
    if key != "model3" or "model3" not in catalog.frames:
        return []
    from . import bottleneck

    ranking = bottleneck.rank_bottlenecks(catalog, key)
    rows = ranking.get("ranking") or []
    top = rows[0] if rows else None
    if top is None:
        return []
    options = simulation.scenario_options(catalog, key)
    offered = {s["key"] for s in options.get("stations", [])}
    station = top["station"]
    if station not in offered:
        return []

    try:
        baseline = simulation._simulate(catalog, ScenarioSpec(name="repair baseline", kind="combined"))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("repair engine could not re-simulate a baseline: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    unit_value = _value_rate(context, "unit_value")
    holding = _value_rate(context, "holding")
    hours = settings.sim_horizon_seconds / 3600.0

    def build(spec: ScenarioSpec, kind: str, title: str, assumptions: List[str]) -> None:
        try:
            scenario = simulation._simulate(catalog, spec)
        except Exception as exc:
            log.warning("repair scenario %s failed: %s", spec.name, exc)
            return
        effect = _sim_effect(baseline, scenario)
        lines: List[Dict[str, Any]] = []
        if unit_value and effect["completed_parts_delta"]:
            amount = effect["completed_parts_delta"] * float(unit_value["value"])
            lines.append(
                {
                    "label": "Completed-parts delta valued at the unit value",
                    "quantity": effect["completed_parts_delta"],
                    "unit": "parts per horizon",
                    "rate": float(unit_value["value"]),
                    "rate_source": unit_value["source"],
                    "amount": round(amount, 2),
                    "note": "measured by re-simulating the documented route",
                }
            )
        deltas = _queue_deltas(baseline, scenario)
        if holding and deltas.get("queue_part_hours_delta"):
            amount = deltas["queue_part_hours_delta"] * float(holding["value"])
            lines.append(
                {
                    "label": "Queue/WIP holding-cost change",
                    "quantity": deltas["queue_part_hours_delta"],
                    "unit": "part-hours per horizon",
                    "rate": float(holding["value"]),
                    "rate_source": holding["source"],
                    "amount": round(amount, 2),
                    "note": "measured by re-simulating the documented route",
                }
            )
        total = sum(l["amount"] for l in lines) if lines else None
        loss = {
            "amount": round(total, 2) if total is not None else None,
            "currency": context["currency"],
            "lines": lines,
            "reason": "" if lines else "no unit value or holding rate is available, so the simulated delta cannot be valued",
        }
        validation = simulation.validate(catalog, scenario, key=key)
        residual = validation.get("mean_abs_residual_pct")
        confidence = "high" if residual is not None and abs(residual) < 5.0 else "medium"
        out.append(
            _candidate(
                repair=title,
                kind=kind,
                target={"station": top["station"], "label": top["label"], "column": top.get("utilization_column")},
                measured={
                    "utilisation": top.get("utilization"),
                    "queue_mean": top.get("queue_mean"),
                    "capacity": top.get("capacity"),
                    "rank": 1,
                },
                loss_reduction=loss,
                intervention=_intervention_cost(kind, context["rate_card"]),
                throughput_effect=effect,
                quality_effect="not modelled by the re-simulation engine",
                evidence=[
                    f"{top['label']} ranks first in the bottleneck ranking (score {top.get('bottleneck_score'):.3f})"
                    if top.get("bottleneck_score") is not None
                    else f"{top['label']} is the leading candidate station",
                    f"simulated queue delta {deltas.get('queue_mean_delta', 0):+.3f} parts",
                    "utilisation, queue and throughput deltas come from the documented-route re-simulation",
                ],
                assumptions=assumptions,
                confidence=confidence,
                scenario=spec.model_dump(),
            )
        )

    build(
        ScenarioSpec(name="capacity relief", kind="capacity_change", station=station, capacity_delta=1),
        "capacity",
        f"Increase capacity at {top['label']} by one parallel resource",
        ["one additional parallel resource is modelled at the station", "the documented route and processing times still apply"],
    )
    if 0.25 <= 0.9 <= 4.0:
        build(
            ScenarioSpec(name="cycle time -10%", kind="processing_time_change", station=station, time_factor=0.9),
            "processing_time",
            f"Reduce cycle time at {top['label']} by 10%",
            ["the station's processing time falls by 10% with no yield change", "downstream demand is unchanged"],
        )
    return out


def _queue_deltas(baseline: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    stations = baseline.get("stations", {})
    out: Dict[str, Any] = {"queue_mean_delta": 0.0, "queue_part_hours_delta": 0.0}
    for name, base in stations.items():
        if name not in scenario.get("stations", {}):
            continue
        delta = float(scenario["stations"][name]["queue_mean"]) - float(base["queue_mean"])
        out["queue_mean_delta"] += delta
        out[f"queue_mean_delta[{name}]"] = delta
    # time-weighted queue: 1 part standing for the whole horizon = horizon part-hours
    out["queue_part_hours_delta"] = out["queue_mean_delta"] * (settings.sim_horizon_seconds / 3600.0)
    return out


def _column_repairs(catalog: Catalog, key: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Levers discovered from the dataset's own numeric columns."""
    df = catalog.model_df(key)
    profile = catalog.profile_map(key)
    columns = [str(c) for c in df.columns]
    numeric = set(_numeric(df))
    out: List[Dict[str, Any]] = []

    def add(
        *,
        repair: str,
        kind: str,
        column: str,
        quantity: float,
        unit: str,
        rate_concept: str,
        reduction: float,
        quality_effect: str,
        assumption: str,
        confidence: str,
    ) -> None:
        rate = _value_rate(context, rate_concept)
        loss = _loss_from_quantity(
            label=repair,
            quantity=quantity,
            unit=unit,
            rate=rate,
            reduction=reduction,
            context=context,
            notes=[assumption],
            observations=int(len(df)),
        )
        out.append(
            _candidate(
                repair=repair,
                kind=kind,
                target={"column": column},
                measured={"column": column, "mean": round(quantity, 4), "unit": unit},
                loss_reduction=loss,
                intervention=_intervention_cost(kind, context["rate_card"]),
                throughput_effect={
                    "available": False,
                    "reason": (
                        "this dataset is not covered by the documented-route re-simulation, so a throughput "
                        "effect cannot be measured - only the measured quantity and its cost are reported"
                    ),
                },
                quality_effect=quality_effect,
                evidence=[
                    f"column '{column}' has mean {quantity:,.4f} {unit} in this dataset",
                    f"cost basis: {rate['source']}" if rate else "no rate available for this quantity",
                ],
                assumptions=[
                    assumption,
                    (
                        f"the {int(len(df)):,} rows are treated as observation periods of one plant, so the loss "
                        "scales with the row count; the per-row figure is reported separately"
                    ),
                ],
                # Without a rate the effect cannot be valued at all, so the
                # candidate is reported as low-confidence evidence, not as a priced repair.
                confidence=confidence if rate else "low",
            )
        )

    # ---- downtime -------------------------------------------------------
    col = _find(columns, "downtime", "down_time")
    if col and col in numeric:
        hours = _hours_from_column(df, col)
        if hours is not None:
            add(
                repair=f"Cut downtime by {DEFAULT_REDUCTIONS['downtime'] * 100:.0f}%",
                kind="downtime",
                column=col,
                quantity=hours,
                unit="hours per row",
                rate_concept="downtime",
                reduction=DEFAULT_REDUCTIONS["downtime"],
                quality_effect="not measured in this dataset",
                assumption=f"column '{col}' is a duration and its name states the unit, so it is read as hours",
                confidence="medium",
            )
        else:
            out.append(
                {
                    "repair": "Cut downtime",
                    "kind": "downtime",
                    "target": {"column": col},
                    "measured": {"column": col, "mean": _mean(df, col), "unit": "not stated"},
                    "expected_quality_effect": "not measured in this dataset",
                    "expected_throughput_effect": {"available": False, "reason": "no simulation for this dataset"},
                    "expected_loss_reduction": {
                        "amount": None,
                        "currency": context["currency"],
                        "lines": [],
                        "reason": f"the unit of '{col}' is not stated in the column name, so hours cannot be assumed",
                    },
                    "intervention_cost": _intervention_cost("downtime", context["rate_card"]),
                    "net_impact": {"amount": None, "currency": context["currency"]},
                    "confidence": "low",
                    "evidence": [f"column '{col}' was detected as a downtime field"],
                    "assumptions": [],
                    "scenario": None,
                }
            )

    # ---- scrap / rework / defects ---------------------------------------
    for concept, needles, unit, label in (
        ("scrap", ("scrap",), "units per row", "scrap"),
        ("rework", ("rework",), "units per row", "rework"),
    ):
        col = _find(columns, *needles, exclude=("cost", "rate", "price", "$"))
        if col and col in numeric:
            quantity = _mean(df, col)
            if quantity is None:
                continue
            add(
                repair=f"Cut {label} by {DEFAULT_REDUCTIONS[concept] * 100:.0f}%",
                kind=concept,
                column=col,
                quantity=quantity,
                unit=unit,
                rate_concept=concept,
                reduction=DEFAULT_REDUCTIONS[concept],
                quality_effect=f"{label} quantity falls by {DEFAULT_REDUCTIONS[concept] * 100:.0f}%",
                assumption=f"column '{col}' counts {label} units per row",
                confidence="medium" if _value_rate(context, concept) else "low",
            )

    # ---- queue / WIP ----------------------------------------------------
    qcol = _find(columns, "queue", "wip")
    if qcol and qcol in numeric:
        quantity = _mean(df, qcol)
        if quantity is not None:
            add(
                repair=f"Reduce queue/WIP by {DEFAULT_REDUCTIONS['queue'] * 100:.0f}%",
                kind="queue",
                column=qcol,
                quantity=quantity,
                unit="parts (mean)",
                rate_concept="holding",
                reduction=DEFAULT_REDUCTIONS["queue"],
                quality_effect="none expected",
                assumption=f"column '{qcol}' holds a queue/WIP count",
                confidence="medium" if _value_rate(context, "holding") else "low",
            )

    # ---- cycle time -----------------------------------------------------
    ccol = _find(columns, "cycle_time", "cycle time")
    if ccol and ccol in numeric:
        quantity = _mean(df, ccol)
        if quantity is not None:
            out.append(
                {
                    "repair": f"Reduce cycle time by {DEFAULT_REDUCTIONS['cycle_time'] * 100:.0f}%",
                    "kind": "processing_time",
                    "target": {"column": ccol},
                    "measured": {"column": ccol, "mean": round(quantity, 4), "unit": _unit_hint(ccol)},
                    "expected_quality_effect": "not measured in this dataset",
                    "expected_throughput_effect": {
                        "available": False,
                        "reason": (
                            "the throughput effect of a faster cycle can only be measured by re-simulation, which "
                            "covers the documented Model 3 route only"
                        ),
                    },
                    "expected_loss_reduction": {
                        "amount": None,
                        "currency": context["currency"],
                        "lines": [],
                        "reason": "a cycle-time change only pays off through throughput, which is not measurable here",
                    },
                    "intervention_cost": _intervention_cost("processing_time", context["rate_card"]),
                    "net_impact": {"amount": None, "currency": context["currency"]},
                    "confidence": "low",
                    "evidence": [f"column '{ccol}' has mean {quantity:,.4f} in this dataset"],
                    "assumptions": [],
                    "scenario": None,
                }
            )
    return out


def _unit_hint(column: str) -> str:
    low = column.lower()
    if low.endswith("_s") or "second" in low:
        return "seconds"
    if low.endswith("_min") or "minute" in low:
        return "minutes"
    if low.endswith("_h") or "hour" in low:
        return "hours"
    return "unit not stated"


def cost_context(catalog: Catalog, key: str, rate_card: Optional[CostRateCard]) -> Dict[str, Any]:
    """Resolve every money rate this dataset can use, with its provenance."""
    df = catalog.model_df(key)
    profile = catalog.profile_map(key)
    dataset_costs = _dataset_costs(df, profile)

    rates: Dict[str, Dict[str, Any]] = {}

    def dataset_rate(concept: str, aliases: tuple) -> None:
        entry = dataset_costs.get(concept)
        if entry and entry.get("mean") is not None:
            rates[concept] = {
                "value": float(entry["mean"]),
                "source": f"dataset column '{entry['column']}' (mean)",
            }

    dataset_rate("scrap", _COST_FIELDS["scrap"])
    dataset_rate("rework", _COST_FIELDS["rework"])
    dataset_rate("downtime", _COST_FIELDS["downtime"])
    dataset_rate("unit_value", _VALUE_FIELDS)

    if rate_card is not None:
        overrides = {
            "scrap": ("cost_per_unit_scrapped", "currency per scrapped unit"),
            "rework": ("rework_cost_per_unit", "currency per reworked unit"),
            "downtime": ("downtime_cost_per_hour", "currency per downtime hour"),
            "holding": ("holding_cost_per_unit_hour", "currency per part-hour"),
            "unit_value": ("margin_per_unit", "currency per part"),
        }
        for concept, (field, describe) in overrides.items():
            value = getattr(rate_card, field, None)
            if value is not None:
                rates[concept] = {"value": float(value), "source": f"rate card: {describe}"}

    currency = (rate_card.currency if rate_card and rate_card.currency else None) or _dataset_currency(df)
    return {
        "rate_card": rate_card,
        "rates": rates,
        "dataset_costs": dataset_costs,
        "currency": currency or "currency not stated",
        "currency_source": (
            "rate card" if rate_card and rate_card.currency else ("dataset column" if _dataset_currency(df) else "not stated anywhere")
        ),
    }


def _dataset_currency(df: pd.DataFrame) -> Optional[str]:
    for col in df.columns:
        if "currency" in str(col).lower():
            try:
                values = df[col].dropna().astype(str).unique().tolist()
                if values:
                    return str(values[0])[:12]
            except Exception:  # pragma: no cover
                return None
    return None


def repair_options(catalog: Catalog, key: str = "model3", rate_card: Optional[CostRateCard] = None) -> Dict[str, Any]:
    """Every supported repair for one dataset, with the cheapest effective one picked."""
    context = cost_context(catalog, key, rate_card)
    candidates = _column_repairs(catalog, key, context) + _sim_repairs(catalog, key, context)

    priced = [
        c
        for c in candidates
        if c["expected_loss_reduction"].get("amount") is not None
        and c["intervention_cost"].get("amount") is not None
        and (c["net_impact"].get("amount") or 0) > 0
    ]
    best = None
    if priced:
        best = max(
            priced,
            key=lambda c: (
                c["net_impact"]["amount"] / max(float(c["intervention_cost"]["amount"]), 1e-9),
                {"high": 3, "medium": 2, "low": 1}[c["confidence"]],
            ),
        )
        ratio = best["net_impact"]["amount"] / max(float(best["intervention_cost"]["amount"]), 1e-9)
        best = {
            **best,
            "benefit_cost_ratio": round(ratio, 2),
            "selection_reason": (
                "highest net benefit per unit of intervention cost among repairs whose effect and cost are both "
                "supported by this dataset"
            ),
        }

    if not candidates:
        status, statement = (
            "unavailable",
            "No supported repair can be proposed: this dataset has no numeric process or cost columns to act on.",
        )
    elif best is None:
        if not any(c["intervention_cost"].get("amount") is not None for c in candidates):
            status = "cost_comparison_unavailable"
            statement = "Repair cost comparison unavailable until a rate card is provided."
        else:
            status = "no_positive_net_impact"
            statement = (
                "Every candidate repair either lacks a valued effect or does not pay back its intervention cost "
                "with the data and rates supplied. Nothing is recommended on this evidence."
            )
    else:
        status = "available"
        statement = (
            f"Cheapest supported effective repair: {best['repair']} "
            f"(net {best['net_impact']['amount']:,.0f} {best['expected_loss_reduction'].get('currency') or ''} "
            f"at a benefit-cost ratio of {best['benefit_cost_ratio']:.1f}x)."
        )

    return {
        "dataset_key": key,
        "status": status,
        "statement": statement,
        "currency": context["currency"],
        "currency_source": context["currency_source"],
        "rate_card_used": bool(rate_card),
        "rates": context["rates"],
        "dataset_cost_columns": context["dataset_costs"],
        "cheapest_supported_effective_repair": best,
        "candidates": candidates,
        "reductions_assumed": DEFAULT_REDUCTIONS,
        "limitations": [
            "Intervention costs are never present in a dataset; they come from the rate card only.",
            "Reduction percentages are stated assumptions, not measurements - change them by supplying your own plan.",
        ],
    }
