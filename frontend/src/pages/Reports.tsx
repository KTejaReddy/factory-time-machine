import { useState } from "react";
import { Link } from "react-router-dom";
import { api, getActiveDataset } from "../lib/api";
import { int, useApi } from "../lib/hooks";
import { Badge, Bullets, Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { AnalysisCompleteCard, RunAnalysisButton, formatDate } from "../components/workspace";

export default function Reports() {
  const key = getActiveDataset();
  const context = useApi(() => api.workspaceDataset(key), [key]);
  const summary = useApi(() => api.analysisSummary(key), [key]);
  const [payload, setPayload] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);

  if (!key) return <Empty>No dataset selected. Upload a dataset first.</Empty>;
  if (context.loading && !context.data) return <Spinner label="Loading case file…" />;
  if (context.error) return <ErrorBox message={context.error} onRetry={context.reload} />;

  const record = context.data?.dataset;
  const runs = context.data?.analysis_runs ?? [];

  const loadJson = async () => {
    setError(null);
    try {
      setPayload(await api.reportJson(key));
      setShowJson(true);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Final report</h1>
          <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            The report is generated from the stored analysis of this dataset, and starts with its name, id and generation
            timestamp — so two downloads can never be confused. Regenerate it after new scenarios or a rate card.
          </p>
        </div>
        <RunAnalysisButton
          label="Refresh analysis"
          onDone={() => {
            summary.reload();
            context.reload();
          }}
        />
      </header>

      {summary.data?.status === "complete" ? (
        <AnalysisCompleteCard summary={summary.data} dataset={record} />
      ) : (
        <Card title="No saved analysis yet" subtitle="The report needs at least one stored analysis run">
          <p className="text-[12.5px] text-[var(--color-ink-dim)]">
            {summary.data?.status === "not_run"
              ? "Run the analysis for this dataset, then download the report."
              : "The analysis summary is unavailable; check the backend log."}
          </p>
        </Card>
      )}

      <Card
        title="Downloads"
        subtitle={`Dataset: ${record?.name ?? "—"} · ID ${record?.id ?? "—"}`}
        actions={<Badge tone={record?.present ? "ok" : "warn"}>{record?.present ? "source present" : "source missing"}</Badge>}
      >
        <div className="flex flex-wrap items-center gap-2">
          <a className={`btn btn-primary ${summary.data?.status === "complete" ? "" : "hidden"}`} href={api.reportUrl(key, "md")}>
            Download final report (Markdown)
          </a>
          <a className={`btn ${summary.data?.status === "complete" ? "" : "hidden"}`} href={api.reportUrl(key, "csv")}>
            Download CSV results
          </a>
          <a className={`btn ${summary.data?.status === "complete" ? "" : "hidden"}`} href={api.reportUrl(key, "json")}>
            Download JSON
          </a>
          <button className="btn" onClick={loadJson} disabled={summary.data?.status !== "complete"}>
            View full payload
          </button>
          {record && (
            <Link className="btn" to={`/dataset/${record.id}`}>
              Open workspace
            </Link>
          )}
        </div>
        <div className="mt-3 text-[11.5px] text-[var(--color-ink-faint)]">
          The CSV is one metric per row (spreadsheet-ready); the JSON carries every section, source label and limitation.
        </div>
        {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      </Card>

      <Card title="Saved analysis runs" subtitle="Reopening a dataset reuses the stored result instead of recomputing it">
        {runs.length === 0 ? (
          <Empty>No stored run for this dataset yet.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>run</th>
                <th>generated</th>
                <th>took</th>
                <th>status</th>
                <th>report</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="mono">#{run.id}</td>
                  <td>{formatDate(run.created_at)}</td>
                  <td className="mono">{run.seconds ? `${run.seconds.toFixed(1)} s` : "—"}</td>
                  <td>{run.status}</td>
                  <td>
                    <a className="link" href={api.reportUrl(key, "md")}>
                      download
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Traceability" subtitle="Every report states these four things at the top">
        <Bullets
          items={[
            `Dataset: ${record?.name ?? "—"}`,
            `Dataset ID: ${record?.id ?? "—"}`,
            `Generated: ${summary.data?.generated_at ? formatDate(summary.data.generated_at) : "—"}`,
            `Rows analysed: ${int(record?.rows ?? 0)} · columns: ${record?.columns ?? 0}`,
          ]}
          tone="dim"
        />
      </Card>

      {showJson && payload && (
        <Card title="Full report payload" subtitle="Exactly what the JSON download contains">
          <pre className="mono max-h-[520px] overflow-auto rounded-md border border-[var(--color-edge)] bg-[var(--color-hull)] p-3 text-[10.5px] leading-snug text-[var(--color-ink-dim)]">
            {JSON.stringify(payload, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  );
}
