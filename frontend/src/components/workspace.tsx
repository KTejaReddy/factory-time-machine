import { useState } from "react";
import { Link } from "react-router-dom";
import { api, getActiveDataset, notifyWorkspaceChanged, type AnalysisSummaryCard, type CapabilityMap, type DatasetRecord } from "../lib/api";
import { int } from "../lib/hooks";
import { Badge, ErrorBox } from "./ui";

/** When a value came from, in one line. */
export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * What a dataset can support, with the reason every switched-off feature is off.
 * A feature is never shown as broken without saying why - and a dataset that
 * supports nothing still renders honestly instead of a wall of zeros.
 */
export function CapabilityChips({ caps, compact = false }: { caps?: CapabilityMap; compact?: boolean }) {
  const available = caps?.available;
  if (!available) return null;
  const reasons = caps?.reasons ?? {};
  const features = Object.entries(available);
  const off = features.filter(([, on]) => !on);
  return (
    <div className={compact ? "" : "mt-2.5"}>
      <div className="flex flex-wrap gap-1.5">
        {features.map(([feature, on]) => (
          <Badge key={feature} tone={on ? "ok" : "warn"} title={on ? undefined : reasons[feature]}>
            {on ? "✓" : "⚠"} {feature}
          </Badge>
        ))}
      </div>
      {off.length > 0 && (
        <details className="mt-1.5 cursor-pointer text-[11.5px] leading-snug text-[var(--color-ink-faint)]">
          <summary>
            {off.length} analysed feature{off.length === 1 ? "" : "s"} unavailable — why?
          </summary>
          <ul className="mt-1 space-y-1">
            {off.map(([feature]) => (
              <li key={feature}>
                <span className="text-[var(--color-ink-dim)]">{feature}:</span>{" "}
                {reasons[feature] ?? "not supported by this dataset"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function statusTone(status: string): "ok" | "warn" | "bad" | "muted" {
  if (status === "analysis_ready") return "ok";
  if (status === "partial_analysis" || status === "images_only") return "warn";
  if (status === "file_missing") return "bad";
  return "muted";
}

/** Runs the saved-analysis computation and reports what happened. */
export function RunAnalysisButton({
  datasetKey,
  onDone,
  label = "Run analysis",
  className = "btn btn-primary",
}: {
  datasetKey?: string;
  onDone?: (payload: Record<string, any>) => void;
  label?: string;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    const key = datasetKey || getActiveDataset();
    setBusy(true);
    setError(null);
    try {
      const payload = await api.runAnalysis(key);
      notifyWorkspaceChanged();
      onDone?.(payload);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <button className={className} disabled={busy || !(datasetKey || getActiveDataset())} onClick={run}>
        {busy ? "Analysing…" : label}
      </button>
      {error && <ErrorBox message={error} />}
    </div>
  );
}

/**
 * The end-of-analysis box: only values that were actually calculated, plus the
 * three downloads. Nothing here is a placeholder.
 */
export function AnalysisCompleteCard({ summary, dataset }: { summary: AnalysisSummaryCard; dataset?: DatasetRecord | null }) {
  if (!summary || summary.status !== "complete") return null;
  const confidence = summary.confidence?.score;
  return (
    <div className="panel border-[var(--color-ok-edge)]">
      <div className="panel-head">
        <div>
          <div className="panel-title">Analysis complete</div>
          <div className="mt-0.5 text-[11.5px] text-[var(--color-ink-faint)]">
            Dataset: <span className="font-medium text-[var(--color-ink-dim)]">{summary.dataset?.name ?? dataset?.name}</span> ·{" "}
            <span className="mono">{summary.dataset?.dataset_id ?? dataset?.id}</span> · generated {formatDate(summary.generated_at)}
          </div>
        </div>
        {confidence !== undefined && <Badge tone={confidence >= 0.7 ? "ok" : confidence >= 0.5 ? "warn" : "muted"}>confidence {(confidence * 100).toFixed(0)}%</Badge>}
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-3">
        <div>
          <div className="eyebrow">Main issue</div>
          <div className="mt-1 text-[12.5px] leading-snug text-[var(--color-ink-dim)]">{summary.main_issue || "—"}</div>
        </div>
        <div>
          <div className="eyebrow">Repair</div>
          <div className="mt-1 text-[12.5px] leading-snug text-[var(--color-ink-dim)]">{summary.repair || "No supported repair yet"}</div>
        </div>
        <div>
          <div className="eyebrow">Estimated impact</div>
          <div className="mt-1 text-[12.5px] leading-snug text-[var(--color-ink-dim)]">{summary.estimated_impact || "—"}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-edge)] px-4 py-3">
        <a className="btn btn-primary" href={api.reportUrl(summary.dataset?.key ?? getActiveDataset(), "md")}>
          Download final report
        </a>
        <a className="btn" href={api.reportUrl(summary.dataset?.key ?? getActiveDataset(), "csv")}>
          Download CSV results
        </a>
        <a className="btn" href={api.reportUrl(summary.dataset?.key ?? getActiveDataset(), "json")}>
          Download JSON
        </a>
        <Link className="btn" to="/reports">
          Report page
        </Link>
        <div className="ml-auto flex items-center gap-2">
          <RunAnalysisButton className="btn" label="Re-run analysis" />
        </div>
      </div>
      {summary.limitations && summary.limitations.length > 0 && (
        <details className="border-t border-[var(--color-edge)] px-4 py-2.5 text-[11.5px] text-[var(--color-ink-faint)]">
          <summary>{summary.limitations.length} recorded limitations</summary>
          <ul className="mt-1.5 space-y-1">
            {summary.limitations.map((line, i) => (
              <li key={i}>• {line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** The identity block used on the workspace page and after an upload. */
export function DatasetIdentity({ record, extras }: { record: DatasetRecord | null | undefined; extras?: Record<string, any> }) {
  if (!record) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <div className="panel-flat p-2.5">
        <div className="eyebrow">Dataset</div>
        <div className="mt-0.5 text-[13px] font-medium text-[var(--color-ink)]">{record.name}</div>
        <div className="mono mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">{record.id}</div>
      </div>
      <div className="panel-flat p-2.5">
        <div className="eyebrow">Uploaded</div>
        <div className="mt-0.5 text-[13px] text-[var(--color-ink-dim)]">{formatDate(record.uploaded_at)}</div>
        <div className="mono mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">{record.source_file || "supplied archive"}</div>
      </div>
      <div className="panel-flat p-2.5">
        <div className="eyebrow">Status</div>
        <div className="mt-1">
          <Badge tone={statusTone(record.status)}>{record.status_label}</Badge>
        </div>
      </div>
      <div className="panel-flat p-2.5">
        <div className="eyebrow">Size</div>
        <div className="mono mt-0.5 text-[13px] text-[var(--color-ink-dim)]">
          {record.kind === "images" ? `${int(record.rows)} images` : `${int(record.rows)} rows × ${record.columns} cols`}
        </div>
        <div className="mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">
          {record.artifacts.analyses} analyses · {record.artifacts.scenarios} scenarios · {record.artifacts.feedback} reviews
          {extras?.has_rate_card || record.artifacts.has_rate_card ? " · rate card" : ""}
        </div>
      </div>
    </div>
  );
}
