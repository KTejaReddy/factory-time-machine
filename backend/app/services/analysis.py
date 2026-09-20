"""Saved analysis: one case file's worth of results, computed once and stored.

The workspace model needs a stable answer to "what did we find for this dataset?".
Re-deriving it on every page visit would be slow and, worse, could produce a
slightly different number than the report the engineer already downloaded. So the
analysis is explicit (the user presses Run analysis), computed here from the
dataset's own capabilities, and persisted with the dataset's id and a timestamp.

Nothing is fabricated: a section the dataset cannot support records
``available: False`` with the reason the capability map already states.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, Optional

from ..schemas import CostRateCard
from . import analytics, bottleneck, economics, forensics, repairs, workspace
from .catalog import Catalog

log = logging.getLogger("ftm.analysis")


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _quality(catalog: Catalog, key: str) -> Dict[str, Any]:
    df = catalog.model_df(key)
    constant = [str(c) for c in df.columns if df[c].nunique(dropna=True) <= 1]
    profile = catalog.profile_map(key)
    return {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "missing_cells": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
        "constant_columns": constant[:20],
        "constant_column_count": len(constant),
        "detected_fields": (profile.get("manufacturing") if profile else None)
        or {
            "note": "uploaded CSV structure is reported on the Datasets page; the supplied archives have documented schemas",
        },
    }


def _process(catalog: Catalog, key: str) -> Dict[str, Any]:
    profile = catalog.profile_map(key)
    if profile:
        columns = profile.get("manufacturing", {}).get("process_vars", []) or []
        summary = (profile.get("stats") or {}).get("numeric_summary", {}) or {}
        return {
            "available": bool(columns),
            "dataset_kind": "uploaded",
            "process_columns": columns,
            "column_stats": {c: summary.get(c, {}) for c in columns},
            "reason": "" if columns else "no numeric process columns were detected in this dataset",
        }
    report = analytics.calibration_report(catalog, key)
    return {
        "available": True,
        "dataset_kind": "supplied_archive",
        "calibration": report,
        "reason": "",
    }


def _anomaly(catalog: Catalog, key: str) -> Dict[str, Any]:
    report = analytics.anomaly_report(catalog, key, top=5)
    if report.get("status") == "unavailable":
        return {"available": False, "reason": report.get("reason"), "method": report.get("method")}
    return {
        "available": True,
        "n_rows": report["n_rows"],
        "n_scored": report["n_scored"],
        "threshold": report["threshold"],
        "anomalous_fraction": report["anomalous_fraction"],
        "top": report["top"][:5],
        "feature_importance": report["feature_importance"][:5],
        "method": report["method"],
        "limitations": report["limitations"],
        "reason": "",
    }


def _production(catalog: Catalog, key: str) -> Dict[str, Any]:
    try:
        ranking = bottleneck.rank_bottlenecks(catalog, key)
        headroom = bottleneck.capacity_headroom(catalog, key)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("production analysis unavailable for %s: %s", key, exc)
        return {"available": False, "reason": f"the production analysis could not be computed: {exc}"}
    rows = ranking.get("ranking") or []
    top = rows[0] if rows else {}
    return {
        "available": bool(rows),
        "reason": "" if rows else "no station in this dataset has a measurable utilisation",
        "method": ranking.get("method"),
        "weights": ranking.get("weights"),
        "bottleneck": {
            "station": top.get("station"),
            "label": top.get("label"),
            "rank": top.get("rank"),
            "score": top.get("bottleneck_score"),
            "utilisation": top.get("utilization"),
            "capacity": top.get("capacity"),
            "queue_mean": top.get("queue_mean"),
            "headroom_pct": (top.get("headroom") or 0) * 100.0 if top.get("headroom") is not None else None,
        },
        "ranking": [
            {"station": r.get("station"), "label": r.get("label"), "score": r.get("bottleneck_score"), "utilisation": r.get("utilization")}
            for r in rows[:5]
        ],
        "unmeasured": [r.get("label") for r in ranking.get("unmeasured") or []],
        "headroom": headroom,
    }


def _forensics(catalog: Catalog, key: str) -> Dict[str, Any]:
    try:
        cases = forensics.list_cases(catalog, key)
    except Exception as exc:
        return {"available": False, "reason": f"forensic cases could not be built: {exc}", "cases": []}
    built = None
    if cases:
        try:
            built = forensics.build_case(catalog, key, cases[0]["case_id"])
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("case %s failed for %s: %s", cases[0]["case_id"], key, exc)
    if built is None:
        return {"available": False, "reason": "no forensic case could be constructed for this dataset", "cases": cases}
    return {
        "available": True,
        "reason": "",
        "cases": cases,
        "leading_case": {
            "case_id": built["case_id"],
            "label": built["label"],
            "scope": built["scope"],
            "statistic": built["statistic"],
            "first_divergence": built.get("first_divergence"),
            "co_occurring": built.get("co_occurring", [])[:3],
            "confidence": built.get("confidence"),
            "confidence_basis": built.get("confidence_basis", []),
            "evidence": built.get("evidence", [])[:6],
        },
    }


def _propagation(catalog: Catalog, key: str) -> Dict[str, Any]:
    from ..db import LinkageAssumption, list_rows

    try:
        assumptions = [row.as_dict() for row in list_rows(LinkageAssumption, limit=50)]
        graph = forensics.propagation_graph(catalog, key, assumptions)
    except Exception as exc:
        return _propagation_unavailable(f"the propagation graph could not be built: {exc}")
    if not graph["nodes"]:
        return _propagation_unavailable(graph.get("linkage_notice") or "no propagation path can be established from the available data")
    observed_edges = [e for e in graph["edges"] if e["status"] == "observed"]
    return {
        "available": True,
        "reason": "",
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "observed_edges": len(observed_edges),
        "assumed_edges": graph.get("assumed_edges", 0),
        "chain": _chain(graph),
        "linkage_notice": graph.get("linkage_notice", ""),
        "limitations": graph.get("limitations", []),
    }


def _propagation_unavailable(reason: str) -> Dict[str, Any]:
    """Keeps the section's shape stable whether or not a path could be built."""
    return {
        "available": False,
        "reason": reason or "no propagation path can be established from the available data",
        "nodes": 0,
        "edges": 0,
        "observed_edges": 0,
        "assumed_edges": 0,
        "chain": [],
        "linkage_notice": "",
        "limitations": [],
    }


def _chain(graph: Dict[str, Any]) -> list:
    """A readable route through the observed edges, for the summary card."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    observed = [e for e in graph["edges"] if e["status"] == "observed"]
    seen: list = []
    for edge in observed:
        for node_id in (edge["source"], edge["target"]):
            node = by_id.get(node_id)
            if node and node["kind"] in {"process", "state", "defect", "outcome"} and node["id"] not in [n["id"] for n in seen]:
                seen.append({"id": node["id"], "label": node["label"], "kind": node["kind"]})
    return seen[:12]


def _economics(catalog: Catalog, key: str, rate_card: Optional[CostRateCard]) -> Dict[str, Any]:
    try:
        return economics.assess(catalog, key, rate_card)
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "reason": f"the economic assessment could not be computed: {exc}", "lines": [], "total": None}


def _confidence(sections: Dict[str, Any]) -> Dict[str, Any]:
    case = (sections.get("forensics") or {}).get("leading_case")
    if case and case.get("confidence") is not None:
        return {
            "score": float(case["confidence"]),
            "basis": ["forensic case confidence on this dataset", *(case.get("confidence_basis") or [])],
        }
    score = 0.3
    basis = []
    if sections.get("production", {}).get("available"):
        score += 0.2
        basis.append("station metrics are measured in this dataset")
    if sections.get("anomaly", {}).get("available"):
        score += 0.2
        basis.append("process anomalies are scored from this dataset's own columns")
    if sections.get("economics", {}).get("available"):
        score += 0.15
        basis.append("the economic figure is backed by measurable quantities and stated rates")
    if sections.get("repairs", {}).get("status") == "available":
        score += 0.15
        basis.append("a repair has both a supported effect and a stated intervention cost")
    return {"score": round(min(score, 0.9), 2), "basis": basis or ["no section produced measurable evidence"]}


def _summary(
    context: Dict[str, Any],
    sections: Dict[str, Any],
    confidence: Dict[str, Any],
    ai: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    quality = sections["quality"]
    anomaly = sections["anomaly"]
    production = sections["production"]
    economic = sections["economics"]
    repair_report = sections["repairs"]

    if anomaly.get("available"):
        top = (anomaly.get("top") or [{}])[0]
        drivers = ", ".join(d.get("feature", "") for d in (top.get("drivers") or [])[:3]) or "no single dominant driver"
        process_text = (
            f"{anomaly['anomalous_fraction'] * 100:.2f}% of scored rows sit beyond the "
            f"{anomaly['threshold']:.2f} distance threshold; the leading outlier is driven by {drivers}"
        )
    else:
        process_text = anomaly.get("reason") or "process anomaly detection is not supported by this dataset"

    if production.get("available"):
        bn = production["bottleneck"]
        production_text = (
            f"{bn.get('label')} is the leading constraint (utilisation "
            f"{bn.get('utilisation'):.3f}, score {bn.get('score'):.3f})"
            if bn.get("utilisation") is not None and bn.get("score") is not None
            else f"{bn.get('label')} is the leading constraint"
        )
    else:
        production_text = production.get("reason") or "production analysis is not supported by this dataset"

    if economic.get("available"):
        economic_text = (
            f"{economic.get('total'):,.0f} {economic.get('currency') or ''} over the documented horizon, "
            "from measured quantities and stated rates"
        )
    else:
        economic_text = economic.get("reason") or "no economic figure could be computed"

    recommendation = repair_report.get("statement") or ""
    if repair_report.get("cheapest_supported_effective_repair"):
        best = repair_report["cheapest_supported_effective_repair"]
        recommendation = f"{best['repair']} - {best['selection_reason']}."
    elif not recommendation and ai:
        recommendation = (ai.get("finding") or {}).get("recommendation") or ""

    limitations: list = []
    for name in ("anomaly", "production", "forensics", "propagation", "economics"):
        section = sections.get(name) or {}
        if not section.get("available", True):
            limitations.append(f"{name}: {section.get('reason')}")
    limitations.extend(repair_report.get("limitations") or [])
    capabilities = context.get("capabilities") or {}
    for name, reason in (capabilities.get("reasons") or {}).items():
        limitations.append(f"{name} unavailable - {reason}")

    return {
        "dataset": context.get("name"),
        "quality": {
            "verdict": (
                "clean" if quality["missing_cells"] == 0 and quality["duplicate_rows"] == 0
                else f"{quality['missing_cells']:,} missing cells, {quality['duplicate_rows']:,} duplicate rows"
            ),
            "main_defect_problem": _main_quality_issue(sections),
        },
        "process": {"verdict": process_text},
        "production": {"verdict": production_text},
        "economic": {"verdict": economic_text, "total": economic.get("total"), "currency": economic.get("currency")},
        "recommendation": {"verdict": recommendation or "no recommendation is supported by this dataset yet"},
        "confidence": confidence,
        "limitations": limitations,
    }


def _main_quality_issue(sections: Dict[str, Any]) -> str:
    forensics_section = sections.get("forensics") or {}
    case = forensics_section.get("leading_case")
    if case:
        return f"{case['label']} ({case['statistic']})"
    anomaly = sections.get("anomaly") or {}
    if anomaly.get("available"):
        return (
            f"{anomaly['anomalous_fraction'] * 100:.2f}% of scored rows are anomalous; "
            "no defect label exists in this dataset to name a physical defect"
        )
    return "this dataset carries no defect labels, so no defect can be named"


def run_analysis(
    catalog: Catalog,
    key: str,
    rate_card: Optional[CostRateCard] = None,
    include_ai: bool = True,
    persist: bool = True,
) -> Dict[str, Any]:
    """Compute and (by default) store the full result set for one dataset."""
    started = time.perf_counter()
    record = workspace.find_record(key)
    capabilities = catalog.capabilities(key)
    available = capabilities.get("available", {}) or {}

    sections: Dict[str, Any] = {"quality": _quality(catalog, key)}
    sections["process"] = _process(catalog, key)
    sections["production"] = _production(catalog, key)
    sections["anomaly"] = _anomaly(catalog, key) if available.get("anomaly") else {
        "available": False,
        "reason": capabilities.get("reasons", {}).get("anomaly", "anomaly detection is not supported by this dataset"),
    }
    sections["forensics"] = _forensics(catalog, key) if available.get("forensics") else {
        "available": False,
        "reason": capabilities.get("reasons", {}).get("forensics", "forensic analysis is not supported by this dataset"),
        "cases": [],
    }
    sections["propagation"] = _propagation(catalog, key)
    sections["economics"] = _economics(catalog, key, rate_card)
    try:
        sections["repairs"] = repairs.repair_options(catalog, key, rate_card)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("repair engine failed for %s: %s", key, exc)
        sections["repairs"] = {"status": "unavailable", "statement": f"the repair engine failed: {exc}", "candidates": []}

    ai_payload = None
    if include_ai:
        try:
            from ..routers.ops import _overview_evidence
            from . import ai_client

            evidence = _overview_evidence(key)
            evidence["repairs"] = sections["repairs"].get("cheapest_supported_effective_repair")
            ai_payload = ai_client.narrative("overview", f"{key}:overview", evidence)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("AI narrative unavailable for %s: %s", key, exc)

    confidence = _confidence(sections)
    payload: Dict[str, Any] = {
        "dataset": {
            "id": record["id"] if record else workspace.dataset_id(key),
            "dataset_id": record["id"] if record else workspace.dataset_id(key),
            "key": key,
            "name": record["name"] if record else key,
            "uploaded_at": record["uploaded_at"] if record else None,
            "status": record["status_label"] if record else "",
            "rows": record["rows"] if record else sections["quality"]["rows"],
            "columns": record["columns"] if record else sections["quality"]["columns"],
        },
        "generated_at": _iso_now(),
        "capabilities": capabilities,
        "sections": sections,
        "summary": _summary(
            {
                "name": record["name"] if record else key,
                "capabilities": capabilities,
            },
            sections,
            confidence,
            ai_payload,
        ),
        "ai": {
            "available": ai_payload is not None,
            "source": (ai_payload or {}).get("source"),
            "provider": (ai_payload or {}).get("provider"),
            "model": (ai_payload or {}).get("model"),
            "generated_at": (ai_payload or {}).get("generated_at"),
            "finding": (ai_payload or {}).get("finding"),
            "fallback_reason": (ai_payload or {}).get("fallback_reason", ""),
        },
        "scenarios_saved": (record or {}).get("artifacts", {}).get("scenarios", 0),
        "feedback_saved": (record or {}).get("artifacts", {}).get("feedback", 0),
        "seconds": round(time.perf_counter() - started, 2),
    }
    if persist:
        try:
            stored = workspace.record_analysis(key, payload, payload["seconds"])
            payload["run_id"] = stored["id"]
            payload["saved_at"] = stored["created_at"]
        except Exception as exc:  # pragma: no cover - storage is best effort
            log.warning("could not store analysis run for %s: %s", key, exc)
            payload["run_id"] = None
    return payload
