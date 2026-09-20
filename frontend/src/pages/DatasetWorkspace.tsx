import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, getActiveDataset, setActiveDataset, type AnalysisSummaryCard, type DatasetRecord } from "../lib/api";
import { int, useApi } from "../lib/hooks";
import { Badge, Bullets, Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { AnalysisCompleteCard, CapabilityChips, DatasetIdentity, RunAnalysisButton, formatDate } from "../components/workspace";

const SECTIONS: Array<{ to: string; label: string; question: string; feature?: string }> = [
  { to: "/", label: "Overview", question: "What was found?" },
  { to: "/inspection", label: "Inspect", question: "What defects were found?" },
  { to: "/forensic", label: "Investigate", question: "Why might they be happening?", feature: "forensics" },
  { to: "/propagation", label: "Problem flow", question: "How is the problem propagating?", feature: "forensics" },
  { to: "/production", label: "Production", question: "Where is the production constraint?", feature: "production" },
  { to: "/economics", label: "Economics", question: "How much is the problem costing?", feature: "economics" },
  { to: "/repairs", label: "Repairs", question: "What possible interventions exist?" },
  { to: "/whatif", label: "What-If", question: "What happens under simulated changes?", feature: "simulation" },
  { to: "/investigator", label: "AI", question: "What does the evidence mean?" },
  { to: "/review", label: "Review", question: "What did the engineer confirm or reject?" },
  { to: "/reports", label: "Reports", question: "Download the final analysis." },
];

export default function DatasetWorkspace() {
  const { id = "" } = useParams();
  const registry = useApi(() => api.workspaceDatasets(), []);
  const [summary, setSummary] = useState<AnalysisSummaryCard | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const record: DatasetRecord | undefined = (registry.data?.datasets ?? []).find(
    (r) => r.id === id || r.key === id,
  );
  const key = record?.key ?? "";
  const context = useApi(() => api.workspaceDataset(id), [id]);

  // Opening a workspace makes that case file the active dataset - all other pages
  // then read the same dataset, which is the whole point of the workspace model.
  useEffect(() => {
    if (record && record.key !== getActiveDataset() && record.present) setActiveDataset(record.key);
  }, [record]);

  const loadSummary = async (datasetKey: string) => {
    try {
      setSummary(await api.analysisSummary(datasetKey));
      setSummaryError(null);
    } catch (err) {
      setSummary(null);
      setSummaryError((err as Error).message);
    }
  };

  useEffect(() => {
    if (key) void loadSummary(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, context.data?.latest_analysis?.id]);

  if (registry.loading && !registry.data) return <Spinner label="Loading workspace…" />;
  if (registry.error) return <ErrorBox message={registry.error} onRetry={registry.reload} />;
  if (!record) {
    return (
      <Empty>
        No dataset with id <span className="mono">{id}</span> exists in this workspace.{" "}
        <Link className="link" to="/datasets">
          Back to your datasets
        </Link>
        .
      </Empty>
    );
  }

  const available = record.capabilities?.available ?? {};
  const latest = context.data?.latest_analysis;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">Dataset workspace</div>
          <h1 className="mt-0.5 text-[22px] font-semibold tracking-tight">{record.name}</h1>
          <p className="mt-1 text-[12.5px] text-[var(--color-ink-dim)]">
            Every analysis, rate card, scenario and review below belongs to this dataset only.
            Uploading or opening another dataset never overwrites this one.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link className="btn" to="/datasets">
            Your datasets
          </Link>
          <RunAnalysisButton
            datasetKey={record.key}
            onDone={() => {
              void loadSummary(record.key);
              context.reload();
            }}
            label={latest ? "Re-run analysis" : "Run analysis"}
          />
        </div>
      </header>

      <DatasetIdentity record={record} />
      <CapabilityChips caps={record.capabilities} compact />

      {summaryError && <ErrorBox message={summaryError} />}

      {summary?.status === "complete" ? (
        <AnalysisCompleteCard summary={summary} dataset={record} />
      ) : (
        <Card
          title="No saved analysis yet"
          subtitle="Running the analysis stores this dataset's own results; it is the object every report is generated from."
        >
          <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            Press <span className="font-medium">Run analysis</span> to compute the quality summary, process anomalies,
            production constraint, forensic finding, propagation summary, economic impact (needs a rate card or cost
            columns), repair options and the AI finding — then store them against{" "}
            <span className="mono">{record.id}</span>.
          </p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <h2 className="eyebrow mb-2">Case sections</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {SECTIONS.map((section) => {
              const off = section.feature ? available[section.feature] === false : false;
              return (
                <Link
                  key={section.to + section.label}
                  to={section.to}
                  className="panel flex items-start justify-between gap-3 p-3 transition-colors hover:bg-[var(--color-hull)]"
                >
                  <div>
                    <div className="text-[13px] font-medium text-[var(--color-ink)]">{section.label}</div>
                    <div className="mt-0.5 text-[11.5px] text-[var(--color-ink-dim)]">{section.question}</div>
                  </div>
                  {off ? (
                    <Badge tone="warn" title={record.capabilities?.reasons?.[section.feature as string]}>
                      unavailable
                    </Badge>
                  ) : (
                    <Badge tone="muted">open</Badge>
                  )}
                </Link>
              );
            })}
          </div>
        </div>

        <div className="space-y-3">
          <Card title="Saved history" subtitle={`${record.artifacts.analyses} analysis runs · ${record.artifacts.scenarios} scenarios · ${record.artifacts.feedback} reviews`}>
            {latest ? (
              <div className="space-y-2 text-[11.5px] text-[var(--color-ink-dim)]">
                <div className="flex items-center justify-between gap-2">
                  <span>Latest analysis</span>
                  <span className="mono text-[var(--color-ink-faint)]">#{latest.id} · {formatDate(latest.created_at)}</span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span>Computation time</span>
                  <span className="mono">{latest.seconds ? `${latest.seconds.toFixed(1)} s` : "—"}</span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span>Rate card</span>
                  {context.data?.rate_card ? <Badge tone="ok">stored for this dataset</Badge> : <Badge tone="warn">not supplied</Badge>}
                </div>
              </div>
            ) : (
              <div className="text-[11.5px] text-[var(--color-ink-dim)]">Nothing stored yet for this dataset.</div>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <Link className="btn" to="/reports">
                Reports
              </Link>
              <Link className="btn" to="/repairs">
                Repairs
              </Link>
            </div>
          </Card>

          {(context.data?.scenarios ?? []).length > 0 && (
            <Card title="Saved scenarios" subtitle="What-If runs stored under this dataset">
              <ul className="space-y-1.5 text-[11.5px] text-[var(--color-ink-dim)]">
                {(context.data?.scenarios ?? []).slice(0, 5).map((run) => (
                  <li key={run.id} className="flex items-center justify-between gap-2">
                    <span className="truncate">{run.name}</span>
                    <span className="mono text-[var(--color-ink-faint)]">
                      {run.result?.summary?.completed_parts !== undefined ? `${int(run.result.summary.completed_parts)} parts` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {record.present === false && (
            <Card title="Source file missing">
              <Bullets
                items={[
                  "The saved history is retained, but the CSV is no longer in data/user_datasets.",
                  "Re-upload the file with the same name to restore analysis for this dataset.",
                ]}
                tone="faint"
              />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
