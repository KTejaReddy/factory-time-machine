import { useEffect, useState } from "react";
import Timeline from "../components/Timeline";
import { Empty, ErrorBox, EvidenceList, FeedbackBar, Spinner, Stat, Bullets } from "../components/ui";
import { api } from "../lib/api";
import { num, pct, useApi } from "../lib/hooks";

export default function Forensic() {
  const cases = useApi(() => api.cases(), []);
  const [caseId, setCaseId] = useState<string>("group:congested");
  const detail = useApi(() => api.case(caseId), [caseId]);
  const [feedbackKey, setFeedbackKey] = useState(0);

  useEffect(() => {
    if (cases.data?.cases?.length && !cases.data.cases.some((c) => c.case_id === caseId)) {
      setCaseId(cases.data.cases[0].case_id);
    }
  }, [cases.data, caseId]);

  const forensicCase = detail.data;
  const first = forensicCase?.first_divergence ?? null;

  return (
    <div className="space-y-6 max-w-5xl">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-bold tracking-tight text-[var(--color-ink)]">Investigation Workspace</h1>
          <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
            Analytical trace of the problem, from origin to production impact.
          </p>
        </div>
        <select className="field min-w-[300px]" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
          {(cases.data?.cases ?? []).map((item) => (
            <option key={item.case_id} value={item.case_id}>
              {item.label.replace("Forensic Case", "Problem Investigation")}
            </option>
          ))}
        </select>
      </header>

      {cases.loading || detail.loading ? (
        <Spinner label="Loading investigation data..." />
      ) : cases.error ? (
        <ErrorBox message={cases.error} onRetry={cases.reload} />
      ) : detail.error ? (
        <ErrorBox message={detail.error} onRetry={detail.reload} />
      ) : !forensicCase ? (
        <Empty>No problem selected.</Empty>
      ) : (
        <div className="space-y-4">
          <div className="panel p-5 space-y-4">
            <h2 className="text-[16px] font-bold text-[var(--color-ink)] flex items-center gap-2">
              <span className="grid place-items-center w-6 h-6 rounded bg-[var(--color-warn-soft)] text-[var(--color-warn)] text-[12px]">1</span>
              Finding Summary
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 bg-[var(--color-hull)] p-4 rounded-md">
              <Stat label="Primary Suspect" value={first ? first.label : "None found"} tone="bad" />
              <Stat label="Assembled parts" value={forensicCase.outcome?.delta_pct !== undefined ? pct(forensicCase.outcome.delta_pct, 2) : "—"} tone={(forensicCase.outcome?.delta_pct ?? 0) < 0 ? "bad" : "ok"} hint="Selection vs average" />
              <Stat label="Stations affected" value={String(forensicCase.co_occurring?.length ?? 0)} />
              <Stat label="Confidence" value={num(forensicCase.confidence * 100, 0) + "%"} tone={forensicCase.confidence >= 0.7 ? "ok" : forensicCase.confidence >= 0.4 ? "warn" : "bad"} />
            </div>
          </div>

          <details className="panel p-5 group" open>
            <summary className="text-[16px] font-bold text-[var(--color-ink)] flex items-center gap-2 cursor-pointer outline-none">
              <span className="grid place-items-center w-6 h-6 rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[12px]">2</span>
              Evidence & Tracing
              <span className="ml-auto text-[var(--color-ink-faint)] group-open:rotate-180 transition-transform">▾</span>
            </summary>
            <div className="mt-5 space-y-5 border-t border-[var(--color-edge)] pt-5">
              <p className="text-[14px] font-medium text-[var(--color-ink-dim)]">
                The problem likely originated at <strong className="text-[var(--color-warn)]">{first ? first.label : "multiple stations"}</strong>.
              </p>
              <EvidenceList items={forensicCase.evidence} />
            </div>
          </details>

          <details className="panel p-5 group" open>
            <summary className="text-[16px] font-bold text-[var(--color-ink)] flex items-center gap-2 cursor-pointer outline-none">
              <span className="grid place-items-center w-6 h-6 rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[12px]">3</span>
              Propagation Timeline
              <span className="ml-auto text-[var(--color-ink-faint)] group-open:rotate-180 transition-transform">▾</span>
            </summary>
            <div className="mt-5 border-t border-[var(--color-edge)] pt-5">
              <Timeline events={forensicCase.timeline} firstStation={first?.station} />
            </div>
          </details>

          <details className="panel p-5 group">
            <summary className="text-[16px] font-bold text-[var(--color-ink)] flex items-center gap-2 cursor-pointer outline-none">
              <span className="grid place-items-center w-6 h-6 rounded bg-[var(--color-accent-soft)] text-[var(--color-accent)] text-[12px]">4</span>
              Limitations & Review
              <span className="ml-auto text-[var(--color-ink-faint)] group-open:rotate-180 transition-transform">▾</span>
            </summary>
            <div className="mt-5 border-t border-[var(--color-edge)] pt-5 grid gap-6 md:grid-cols-2">
              <div>
                <h3 className="text-[13px] font-semibold mb-3 uppercase tracking-wider text-[var(--color-ink-faint)]">What We Don't Know</h3>
                <Bullets items={forensicCase.limitations} />
              </div>
              <div>
                <h3 className="text-[13px] font-semibold mb-3 uppercase tracking-wider text-[var(--color-ink-faint)]">Human Review</h3>
                <div className="bg-[var(--color-hull)] p-4 rounded-md">
                  <p className="mb-4 text-[13px] text-[var(--color-ink-dim)]">Was this analysis useful?</p>
                  <FeedbackBar
                    key={`${caseId}-${feedbackKey}`}
                    findingId={`forensic:${forensicCase.case_id}`}
                    findingKind="forensic_case"
                    findingTitle={`${forensicCase.label}`}
                    payload={{
                      case_id: forensicCase.case_id,
                      confidence: forensicCase.confidence,
                    }}
                    onDone={() => setFeedbackKey((k) => k + 1)}
                  />
                </div>
              </div>
            </div>
          </details>

          <details className="mt-2 text-[12px] text-[var(--color-ink-dim)] cursor-pointer pl-2">
            <summary className="outline-none hover:text-[var(--color-ink)]">How this was calculated ▾</summary>
            <div className="mt-2 p-3 bg-white border border-[var(--color-edge)] rounded shadow-sm space-y-2 max-w-lg">
              <p><strong>Algorithm:</strong> Process-route order and partial correlation.</p>
              <p><strong>Method:</strong> It traces unusual changes (using robust Z-scores or Cohen's d) along the documented flow of the factory.</p>
              <p><strong>Note:</strong> This shows statistical association, not absolute proof of causation.</p>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
