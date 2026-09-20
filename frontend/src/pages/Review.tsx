import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Spinner } from "../components/ui";
import { api, getActiveDataset } from "../lib/api";
import { int, useApi } from "../lib/hooks";
import { formatDate } from "../components/workspace";

const DECISION_META: Record<string, { label: string; tone: "ok" | "bad" | "warn" }> = {
  confirmed: { label: "✓ Confirmed", tone: "ok" },
  rejected: { label: "✕ Rejected", tone: "bad" },
  needs_review: { label: "⚠ Needs review", tone: "warn" },
};

export default function Review() {
  const key = getActiveDataset();
  const summary = useApi(() => api.reviewSummary(key), [key]);
  const feedback = useApi(() => api.feedbackList(key, 100), [key]);
  const context = useApi(() => api.workspaceDataset(key), [key]);

  const counts = summary.data?.counts ?? {};
  const datasetName = context.data?.dataset?.name ?? key;
  if (!key) return <Empty>No dataset selected. Upload a dataset first.</Empty>;

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      <header className="border-b border-[var(--color-edge)] pb-5">
        <h1 className="text-[24px] font-bold tracking-tight text-[var(--color-ink)]">Feedback & Review — {datasetName}</h1>
        <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
          Every decision below was recorded against this dataset's findings. Switching datasets switches this list —
          verdicts from another case file never appear here.
        </p>
        <div className="mono mt-1 text-[11px] text-[var(--color-ink-faint)]">{key}</div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-accent)]">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Total Decisions</div>
          <div className="text-[28px] font-bold text-[var(--color-ink)]">{int(summary.data?.total ?? 0)}</div>
        </div>
        <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-ok)]">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Confirmed</div>
          <div className="text-[28px] font-bold text-[var(--color-ok)]">{int(counts.confirmed ?? 0)}</div>
        </div>
        <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-bad)]">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Rejected</div>
          <div className="text-[28px] font-bold text-[var(--color-bad)]">{int(counts.rejected ?? 0)}</div>
        </div>
        <div className="panel p-5 bg-[var(--color-surface)] border-l-4 border-l-[var(--color-warn)]">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-ink-faint)] font-semibold mb-1">Needs Review</div>
          <div className="text-[28px] font-bold text-[var(--color-warn)]">{int(counts.needs_review ?? 0)}</div>
        </div>
      </div>

      <div className="panel p-0 overflow-hidden shadow-sm">
        <div className="panel-head border-b border-[var(--color-edge)] bg-[var(--color-hull)]">
          <h2 className="panel-title flex items-center gap-2">
            <span className="grid place-items-center w-5 h-5 rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[12px]">📝</span>
            Decision History
          </h2>
        </div>
        
        <div className="p-6">
          {feedback.loading ? (
            <div className="flex justify-center py-12"><Spinner label="Loading history..." /></div>
          ) : feedback.error ? (
            <ErrorBox message={feedback.error} onRetry={feedback.reload} />
          ) : feedback.data?.length ? (
            <div className="space-y-4">
              {feedback.data.map((item) => {
                const meta = DECISION_META[item.decision] ?? DECISION_META.needs_review;
                return (
                  <div key={item.id} className="panel p-5 bg-[var(--color-surface)] border border-[var(--color-edge)] hover:border-[var(--color-edge-strong)] transition-colors">
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                      <span className="text-[12.5px] text-[var(--color-ink-faint)] font-mono">
                        {new Date(item.created_at).toLocaleString()}
                      </span>
                    </div>
                    
                    <div className="text-[15px] font-semibold text-[var(--color-ink)]">{item.finding_title}</div>
                    
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-[var(--color-ink-dim)]">
                      <span>
                        <span className="mr-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-ink-faint)]">Dataset:</span>
                        {item.dataset_key || "legacy (recorded before dataset case files)"}
                      </span>
                      <span className="mono text-[11px] text-[var(--color-ink-faint)]">saved {formatDate(item.created_at)}</span>
                    </div>

                    {item.note && (
                      <p className="mt-3 text-[13.5px] italic text-[var(--color-ink-dim)] bg-[var(--color-hull)] p-3 rounded-md border-l-2 border-l-[var(--color-edge-strong)]">
                        "{item.note}"
                      </p>
                    )}
                    
                    <details className="mt-4 text-[12px] text-[var(--color-ink-dim)] cursor-pointer outline-none">
                      <summary className="font-semibold text-[var(--color-ink)] hover:text-[var(--color-accent)] transition-colors">Data Details ▾</summary>
                      <pre className="mt-2 max-h-[200px] overflow-auto rounded-md bg-[var(--color-void)] p-3 text-[11px] border border-[var(--color-edge)]">
                        {JSON.stringify(item.payload, null, 2)}
                      </pre>
                    </details>
                  </div>
                );
              })}
            </div>
          ) : (
            <Empty>
              No engineer decision recorded for {datasetName} yet. Review a finding from the{" "}
              <Link className="link" to="/forensic">Investigate</Link> or{" "}
              <Link className="link" to="/investigator">AI</Link> page.
            </Empty>
          )}
        </div>
      </div>
    </div>
  );
}
