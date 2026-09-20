"""Final report assembly and rendering.

The report is built from **stored analysis results** (or a fresh run when none
exists yet) plus the dataset's saved artifacts - it never recomputes a private
version of a number. Every render starts with the dataset's name, id and
generation timestamp, so two downloaded reports can never be confused.

Formats: JSON (machine), Markdown (readable), CSV (tidy key/value rows).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from typing import Any, Dict, List, Optional

from ..db import FeedbackItem, list_rows
from ..schemas import CostRateCard
from . import analysis, workspace
from .catalog import Catalog

log = logging.getLogger("ftm.reports")

FORMATS = ("json", "md", "csv")


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _feedback_for(key: str, limit: int = 100) -> List[Dict[str, Any]]:
    rows = [row.as_dict() for row in list_rows(FeedbackItem, limit=limit)]
    return [r for r in rows if r["dataset_key"] == key]


def build_report(
    catalog: Catalog,
    key: str,
    rate_card: Optional[CostRateCard] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Assemble the final report payload for one dataset."""
    context = workspace.context(key)
    stored = context.get("latest_analysis")
    if refresh or stored is None:
        payload = analysis.run_analysis(catalog, key, rate_card=rate_card)
        analysis_payload = {k: payload[k] for k in payload if k != "payload"}
        created_at = payload.get("saved_at")
    else:
        analysis_payload = stored["payload"]
        created_at = stored["created_at"]

    record = context.get("dataset") or {}
    return {
        "dataset": {
            "id": record.get("id") or workspace.dataset_id(key),
            "dataset_id": record.get("id") or workspace.dataset_id(key),
            "key": key,
            "name": record.get("name") or key,
            "uploaded_at": record.get("uploaded_at"),
            "status": record.get("status_label") or record.get("status"),
            "rows": record.get("rows", analysis_payload.get("dataset", {}).get("rows")),
            "columns": record.get("columns", analysis_payload.get("dataset", {}).get("columns")),
            "source_file": record.get("source_file"),
        },
        "generated_at": _iso_now(),
        "analysis_saved_at": created_at,
        "analysis_run_id": (stored or {}).get("id"),
        "capabilities": analysis_payload.get("capabilities", {}),
        "sections": analysis_payload.get("sections", {}),
        "summary": analysis_payload.get("summary", {}),
        "ai": analysis_payload.get("ai", {}),
        "rate_card": context.get("rate_card"),
        "scenarios": [
            {
                "id": s["id"],
                "name": s["name"],
                "created_at": s["created_at"],
                "engine": s["engine"],
                "summary": (s.get("result") or {}).get("summary"),
                "scenario": s.get("scenario"),
            }
            for s in context.get("scenarios", [])[:10]
        ],
        "feedback": _feedback_for(key, limit=50),
        "limitations": (analysis_payload.get("summary") or {}).get("limitations", []),
        "advisory": (
            "Advisory decision support only. Every number in this report traces to the dataset named above, "
            "to a stated rate supplied by the user, or to a simulation of the documented route. No equipment "
            "is controlled and no value is invented."
        ),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    dataset = report["dataset"]
    sections = report["sections"]
    summary = report["summary"]
    capabilities = report["capabilities"].get("available", {}) if report.get("capabilities") else {}
    out: List[str] = []
    out.append(f"# Analysis report - {dataset['name']}")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append(f"| Dataset | {dataset['name']} |")
    out.append(f"| Dataset ID | `{dataset['id']}` |")
    out.append(f"| Catalog key | `{dataset['key']}` |")
    out.append(f"| Source file | {dataset.get('source_file') or '-'} |")
    out.append(f"| Uploaded | {dataset.get('uploaded_at') or '-'} |")
    out.append(f"| Status | {dataset.get('status') or '-'} |")
    out.append(f"| Rows x columns | {_fmt(dataset.get('rows'), 0)} x {_fmt(dataset.get('columns'), 0)} |")
    out.append(f"| Report generated | {report['generated_at']} |")
    out.append(f"| Analysis run | #{report.get('analysis_run_id')} ({report.get('analysis_saved_at') or '-'}) |")
    out.append("")

    out.append("## Available analysis")
    out.append("")
    out.append("| Capability | Available | Reason when unavailable |")
    out.append("|---|---|---|")
    reasons = (report.get("capabilities") or {}).get("reasons", {}) or {}
    for name in ("vision", "production", "anomaly", "forensics", "economics", "simulation"):
        if name not in capabilities and name not in reasons:
            continue
        out.append(f"| {name} | {'yes' if capabilities.get(name) else 'no'} | {reasons.get(name, '')} |")
    out.append("")

    out.append("## Dataset summary")
    out.append("")
    for label, entry in (
        ("Quality", summary.get("quality", {})),
        ("Process", summary.get("process", {})),
        ("Production", summary.get("production", {})),
        ("Economic", summary.get("economic", {})),
        ("Recommendation", summary.get("recommendation", {})),
    ):
        out.append(f"- **{label}:** {entry.get('verdict', '-')}")
    confidence = summary.get("confidence", {}) or {}
    out.append(f"- **Confidence:** {_fmt(confidence.get('score'), 2)} ({'; '.join(confidence.get('basis') or []) or 'no basis recorded'})")
    out.append("")

    quality = sections.get("quality", {})
    out.append("## Data quality")
    out.append("")
    out.append(f"- Rows: {_fmt(quality.get('rows'), 0)}")
    out.append(f"- Columns: {_fmt(quality.get('columns'), 0)}")
    out.append(f"- Missing cells: {_fmt(quality.get('missing_cells'), 0)}")
    out.append(f"- Duplicate rows: {_fmt(quality.get('duplicate_rows'), 0)}")
    out.append(f"- Constant columns: {_fmt(quality.get('constant_column_count'), 0)}")
    detected = quality.get("detected_fields") or {}
    if detected.get("stations") is not None:
        out.append(f"- Detected station columns: {', '.join(detected.get('stations') or []) or 'none'}")
        out.append(f"- Detected process columns: {', '.join(detected.get('process_vars') or []) or 'none'}")
        out.append(f"- Detected cost columns: {', '.join(detected.get('cost_fields') or []) or 'none'}")
    out.append("")

    anomaly = sections.get("anomaly", {})
    out.append("## Process anomalies")
    out.append("")
    if anomaly.get("available"):
        out.append(f"- Method: {anomaly.get('method')}")
        out.append(f"- Scored rows: {_fmt(anomaly.get('n_scored'), 0)} of {_fmt(anomaly.get('n_rows'), 0)}")
        out.append(f"- Threshold: {_fmt(anomaly.get('threshold'))}; anomalous fraction: {_fmt(anomaly.get('anomalous_fraction'))}")
        for run in (anomaly.get("top") or [])[:3]:
            drivers = ", ".join(f"{d.get('feature')} ({d.get('direction')} z={d.get('z')})" for d in (run.get("drivers") or [])[:3])
            out.append(f"- Row {run.get('run_index')}: score {_fmt(run.get('score'))} - {drivers or 'no driver beyond threshold'}")
    else:
        out.append(f"- Not available: {anomaly.get('reason')}")
    out.append("")

    production = sections.get("production", {})
    out.append("## Production constraint")
    out.append("")
    if production.get("available"):
        out.append("| Rank | Station | Utilisation | Capacity | Queue mean | Score |")
        out.append("|---|---|---|---|---|---|")
        for row in production.get("ranking") or []:
            out.append(
                f"| {row.get('rank')} | {row.get('label')} | {_fmt(row.get('utilisation'))} | "
                f"{_fmt(row.get('capacity'), 0)} | {_fmt(row.get('queue_mean'))} | {_fmt(row.get('score'))} |"
            )
        headroom = production.get("headroom") or {}
        out.append("")
        out.append(f"- First station to saturate: {headroom.get('first_station_to_saturate') or '-'}")
    else:
        out.append(f"- Not available: {production.get('reason')}")
    out.append("")

    forensics_section = sections.get("forensics", {})
    out.append("## Forensic finding")
    out.append("")
    if forensics_section.get("available") and forensics_section.get("leading_case"):
        case = forensics_section["leading_case"]
        out.append(f"- Case: {case['label']} ({case['case_id']})")
        out.append(f"- Statistic: {case['statistic']}")
        first = case.get("first_divergence") or {}
        out.append(f"- First divergence: {first.get('label') or '-'} ({first.get('metric') or '-'}, z={_fmt(first.get('z'), 2)})")
        out.append(f"- Confidence: {_fmt(case.get('confidence'), 2)}")
        for line in case.get("evidence") or []:
            out.append(f"- Evidence: {line}")
    else:
        out.append(f"- Not available: {forensics_section.get('reason')}")
    out.append("")

    propagation = sections.get("propagation", {})
    out.append("## Failure propagation")
    out.append("")
    if propagation.get("available"):
        chain = " -> ".join(node.get("label", "") for node in propagation.get("chain") or [])
        out.append(f"- Nodes: {propagation.get('nodes')}; edges: {propagation.get('edges')} (assumed: {propagation.get('assumed_edges')})")
        out.append(f"- Route: {chain or 'no ordered chain could be established'}")
        if propagation.get("linkage_notice"):
            out.append(f"- {propagation['linkage_notice']}")
    else:
        out.append(f"- Not available: {propagation.get('reason')}")
    out.append("")

    economic = sections.get("economics", {})
    out.append("## Economic impact")
    out.append("")
    if economic.get("available"):
        out.append(f"- Total: {_fmt(economic.get('total'), 2)} {economic.get('currency') or ''}")
        out.append("")
        out.append("| Line | Quantity | Unit | Rate | Amount | Quantity source | Rate source |")
        out.append("|---|---|---|---|---|---|---|")
        for line in economic.get("lines") or []:
            out.append(
                f"| {line.get('label')} | {_fmt(line.get('quantity'))} | {line.get('unit')} | {_fmt(line.get('rate'))} | "
                f"{_fmt(line.get('amount'))} | {line.get('quantity_source')} | {line.get('rate_source')} |"
            )
    else:
        out.append(f"- Not available: {economic.get('reason')}")
    out.append("")

    repair_report = sections.get("repairs", {})
    out.append("## Repair options")
    out.append("")
    out.append(f"{repair_report.get('statement', '-')}")
    out.append("")
    out.append("| Repair | Expected loss reduction | Intervention cost | Net impact | Confidence |")
    out.append("|---|---|---|---|---|")
    for candidate in repair_report.get("candidates") or []:
        loss = candidate.get("expected_loss_reduction") or {}
        cost = candidate.get("intervention_cost") or {}
        net = candidate.get("net_impact") or {}
        out.append(
            f"| {candidate.get('repair')} | {_fmt(loss.get('amount'), 2)} | {_fmt(cost.get('amount'), 2)} | "
            f"{_fmt(net.get('amount'), 2)} | {candidate.get('confidence')} |"
        )
    best = repair_report.get("cheapest_supported_effective_repair")
    if best:
        out.append("")
        out.append(f"**Cheapest supported effective repair:** {best.get('repair')} "
                   f"(net {_fmt((best.get('net_impact') or {}).get('amount'), 2)}, "
                   f"benefit-cost ratio {_fmt(best.get('benefit_cost_ratio'), 2)}x)")
    out.append("")

    scenarios = report.get("scenarios") or []
    out.append("## What-if scenarios")
    out.append("")
    if scenarios:
        out.append("| # | Scenario | Saved | Completed parts | Delta |")
        out.append("|---|---|---|---|---|")
        for run in scenarios:
            summary_run = run.get("summary") or {}
            out.append(
                f"| {run['id']} | {run['name']} | {run.get('created_at')} | "
                f"{_fmt(summary_run.get('completed_parts'), 0)} | {_fmt(summary_run.get('throughput_delta_parts'), 0)} |"
            )
    else:
        out.append("- No saved scenario for this dataset yet.")
    out.append("")

    ai = report.get("ai") or {}
    out.append("## AI finding")
    out.append("")
    if ai.get("available") and ai.get("finding"):
        finding = ai["finding"]
        out.append(f"- Source: {ai.get('source')} ({ai.get('provider')} {ai.get('model')})")
        out.append(f"- Finding: {finding.get('finding')}")
        for line in finding.get("evidence") or []:
            out.append(f"- Evidence: {line}")
        out.append(f"- Recommendation: {finding.get('recommendation')}")
        out.append(f"- Confidence: {_fmt(finding.get('confidence'), 2)}")
    else:
        out.append("- No AI narrative is stored for this dataset.")
    out.append("")

    feedback = report.get("feedback") or []
    out.append("## Human feedback")
    out.append("")
    if feedback:
        out.append("| Finding | Decision | Engineer | Note |")
        out.append("|---|---|---|---|")
        for item in feedback[:25]:
            out.append(
                f"| {item.get('finding_title')} | {item.get('decision')} | {item.get('engineer')} | {item.get('note')} |"
            )
    else:
        out.append("- No engineer review recorded for this dataset.")
    out.append("")

    out.append("## Limitations")
    out.append("")
    for line in report.get("limitations") or ["None recorded."]:
        out.append(f"- {line}")
    out.append("")
    out.append(f"_{report.get('advisory', '')}_")
    out.append("")
    return "\n".join(out)


def render_csv(report: Dict[str, Any]) -> str:
    """Tidy one-metric-per-row results, the shape a spreadsheet wants."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["dataset", "dataset_id", "generated_at", "section", "item", "value", "source"])
    dataset = report["dataset"]
    common = [dataset["name"], dataset["id"], report["generated_at"]]

    def row(section: str, item: str, value: Any, source: str = "dataset") -> None:
        writer.writerow([*common, section, item, _fmt(value, 4), source])

    row("dataset", "catalog_key", dataset["key"], "registry")
    row("dataset", "uploaded_at", dataset.get("uploaded_at"), "registry")
    row("dataset", "status", dataset.get("status"), "registry")
    row("dataset", "rows", dataset.get("rows"), "dataset")
    row("dataset", "columns", dataset.get("columns"), "dataset")

    capabilities = report.get("capabilities") or {}
    for name, value in (capabilities.get("available") or {}).items():
        reason = (capabilities.get("reasons") or {}).get(name, "")
        row("capability", name, "yes" if value else "no", reason or "capability map")

    quality = report["sections"].get("quality", {})
    for field in ("rows", "columns", "missing_cells", "duplicate_rows", "constant_column_count"):
        row("quality", field, quality.get(field), "dataset")

    def walk(section: str, data: Dict[str, Any], prefix: str = "") -> None:
        for key, value in data.items():
            if isinstance(value, dict):
                walk(section, value, f"{prefix}{key}.")
            elif isinstance(value, list):
                if value and not isinstance(value[0], (dict, list)):
                    row(section, f"{prefix}{key}", ",".join(str(_fmt(v)) for v in value), "analysis")
                else:
                    for i, entry in enumerate(value):
                        if isinstance(entry, dict):
                            walk(section, entry, f"{prefix}{key}[{i}].")
            else:
                row(section, f"{prefix}{key}", value, "analysis")

    for name in ("anomaly", "production", "forensics", "propagation", "economics", "process"):
        walk(name, report["sections"].get(name) or {})

    summary = report.get("summary", {})
    for name in ("quality", "process", "production", "economic", "recommendation"):
        row("summary", name, (summary.get(name) or {}).get("verdict"), "analysis")
    row("summary", "confidence", (summary.get("confidence") or {}).get("score"), "analysis")

    repair_report = report["sections"].get("repairs", {})
    for candidate in repair_report.get("candidates") or []:
        row("repair", candidate.get("repair"), (candidate.get("expected_loss_reduction") or {}).get("amount"), "analysis")
        row("repair", f"{candidate.get('repair')} - intervention cost", (candidate.get("intervention_cost") or {}).get("amount"), "rate card")
        row("repair", f"{candidate.get('repair')} - net impact", (candidate.get("net_impact") or {}).get("amount"), "analysis")
    best = repair_report.get("cheapest_supported_effective_repair")
    if best:
        row("repair", "cheapest_supported_effective_repair", best.get("repair"), "analysis")

    for run in report.get("scenarios") or []:
        summary_run = run.get("summary") or {}
        row("scenario", f"{run['name']} - completed parts", summary_run.get("completed_parts"), "simulation")
        row("scenario", f"{run['name']} - delta", summary_run.get("throughput_delta_parts"), "simulation")

    for line in report.get("limitations") or []:
        row("limitation", line, "", "analysis")
    return buffer.getvalue()


def render(report: Dict[str, Any], fmt: str) -> tuple:
    """Return ``(media_type, filename, body)`` for one format."""
    dataset = report["dataset"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"report-{dataset['id']}-{stamp}"
    if fmt == "json":
        import json

        return "application/json", f"{base}.json", json.dumps(report, indent=2, default=str)
    if fmt == "csv":
        return "text/csv", f"{base}.csv", render_csv(report)
    if fmt == "md":
        return "text/markdown", f"{base}.md", render_markdown(report)
    raise ValueError(f"unsupported format '{fmt}' (expected one of {', '.join(FORMATS)})")
