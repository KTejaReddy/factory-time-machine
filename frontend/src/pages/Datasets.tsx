import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Bullets, Card, Empty, ErrorBox, Spinner, Stat } from "../components/ui";
import { api, notifyWorkspaceChanged, setActiveDataset, type CapabilityMap } from "../lib/api";
import { int, num, useApi } from "../lib/hooks";
import { formatDate, statusTone } from "../components/workspace";
import { useWorkspaceDatasets } from "../components/Layout";

const severityTone = (severity: string) => (severity === "error" ? "bad" : severity === "warning" ? "warn" : "muted");

/**
 * The dataset history: one row per case file, newest first.
 *
 * Opening a dataset switches every page to it; nothing is deleted or overwritten
 * when another dataset is uploaded, and a dataset whose source file is gone keeps
 * its saved history with a badge saying so.
 */
function DatasetHistory() {
  const { records, reload } = useWorkspaceDatasets();
  const [error, setError] = useState<string | null>(null);

  const remove = async (key: string, name: string) => {
    if (!window.confirm(`Remove the CSV for "${name}"? Its saved analysis and history are kept.`)) return;
    try {
      await api.deleteDataset(key);
      notifyWorkspaceChanged();
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Card
      title="Your datasets"
      subtitle={`${records.length} case file${records.length === 1 ? "" : "s"} — each keeps its own analysis, rate card, scenarios, AI findings and feedback`}
      actions={
        <button className="btn" onClick={reload}>
          Refresh
        </button>
      }
    >
      {error && <ErrorBox message={error} />}
      {records.length === 0 ? (
        <Empty>No dataset yet. Upload a CSV on the Overview page to create the first case file.</Empty>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {records.map((record) => (
              <div key={record.key} className="glass-panel p-5 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-[10px] font-bold tracking-widest uppercase text-[var(--color-ink-faint)]">DATASET</div>
                    <Badge tone={statusTone(record.status)}>{record.status_label}</Badge>
                  </div>
                  <div className="text-[16px] font-bold tracking-tight text-[var(--color-ink)] mb-1">{record.name}</div>
                  <div className="text-[12px] text-[var(--color-ink-dim)] mb-4">{record.source_file || "supplied archive"}</div>
                  
                  <div className="space-y-1 text-[11.5px] text-[var(--color-ink-dim)]">
                    <div className="flex justify-between">
                      <span>Uploaded</span>
                      <span className="font-medium text-[var(--color-ink)]">{formatDate(record.uploaded_at)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Size</span>
                      <span className="font-medium text-[var(--color-ink)]">{record.kind === "images" ? `${int(record.rows)} images` : `${int(record.rows)} × ${record.columns}`}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Artifacts</span>
                      <span className="font-medium text-[var(--color-ink)]">{record.artifacts.analyses} analysis</span>
                    </div>
                  </div>
                </div>
                <div className="mt-5 pt-4 border-t border-[rgba(20,180,100,0.15)] flex gap-2">
                  <Link
                    className="btn btn-primary flex-1 py-2 uppercase tracking-wide font-bold"
                    to={`/dataset/${record.id}`}
                    onClick={() => {
                      if (record.present) {
                        setActiveDataset(record.key);
                        notifyWorkspaceChanged();
                      }
                    }}
                  >
                    Open Workspace
                  </Link>
                  {record.key.startsWith("user:") && record.present && (
                    <button className="btn" title="Delete" onClick={() => remove(record.key, record.name)}>
                      🗑️
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <p className="text-[11.5px] text-[var(--color-ink-faint)]">
            Deleting removes the CSV only. The dataset's saved analysis, scenarios and feedback stay in its case file,
            flagged "source file missing".
          </p>
        </div>
      )}
    </Card>
  );
}

/**
 * What a dataset can support, with the reason every switched-off feature is off.
 * A feature is never shown as broken without saying why - and a dataset that
 * supports nothing still renders honestly instead of a wall of zeros.
 */
function CapabilityChips({ caps }: { caps?: CapabilityMap }) {
  const available = caps?.available;
  if (!available) return null;
  const reasons = caps?.reasons ?? {};
  const features = Object.entries(available);
  const off = features.filter(([, on]) => !on);
  return (
    <div className="mt-2.5">
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

export default function Datasets() {
  const overview = useApi(() => api.overview(), []);
  const columns = useApi(() => api.columns(), []);
  const quality = useApi(() => api.quality(), []);
  const linkage = useApi(() => api.linkage(), []);
  const design = useApi(() => api.design(), []);
  const [documentedOnly, setDocumentedOnly] = useState(false);

  const building = overview.data?.status.state !== "ready";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Datasets, schema & quality</h1>
          <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
            What was actually found in the supplied files, what was measured rather than assumed, and what is missing.
            The same detail is written to DATASET_SCHEMA.md.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => api.process(false).then(() => overview.reload())}>
            Re-process datasets
          </button>
          <button
            className="btn btn-warn"
            onClick={() => api.process(true).then(() => overview.reload())}
            title="Ignore the cached parquet/JSON and re-read the source CSVs"
          >
            Force rebuild
          </button>
        </div>
      </header>

      {overview.loading ? (
        <Spinner />
      ) : overview.error ? (
        <ErrorBox message={overview.error} onRetry={overview.reload} />
      ) : (
        <>
          <DatasetHistory />

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Catalog state"
              value={building ? "building" : overview.data?.status.state === "failed" ? "failed" : "ready"}
              tone={overview.data?.status.state === "ready" ? "ok" : "warn"}
              hint={overview.data?.status.seconds ? `${num(overview.data.status.seconds, 1)} s to build` : undefined}
            />
            <Stat label="Datasets" value={int(overview.data?.datasets.length ?? 0)} />
            <Stat label="Quality notes" value={int(overview.data?.issues.length ?? 0)} tone="warn" />
            <Stat label="Cross-dataset join keys" value="none" tone="bad" hint="measured: no shared identifier of any kind" />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            {(overview.data?.datasets ?? []).map((ds) => (
              <Card key={ds.key} title={ds.label} subtitle={ds.source_file} actions={<Badge tone={ds.present ? "ok" : "bad"}>{ds.present ? "present" : "missing"}</Badge>}>
                <div className="mono mb-2 text-[11.5px] text-[var(--color-ink-faint)]">
                  {ds.kind === "images" ? `${int(ds.rows)} images` : `${int(ds.rows)} rows × ${ds.columns} columns`}
                </div>
                <p className="text-[12px] leading-relaxed text-[var(--color-ink-dim)]">{ds.description}</p>
                <CapabilityChips caps={ds.capabilities} />
                {Object.keys(ds.extras?.classes ?? {}).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {Object.entries(ds.extras.classes as Record<string, number>).map(([cls, count]) => (
                      <Badge key={cls} tone="muted">
                        {cls}: {count.toLocaleString()}
                      </Badge>
                    ))}
                  </div>
                )}
                {ds.warnings?.length > 0 && (
                  <div className="mt-2.5">
                    <Bullets items={ds.warnings} tone="faint" />
                  </div>
                )}
              </Card>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Detected data-quality issues" subtitle="Counts are measured on the loaded frames">
              <ul className="space-y-2">
                {(overview.data?.issues ?? []).map((issue, i) => (
                  <li key={i} className="flex gap-2.5">
                    <Badge tone={severityTone(issue.severity) as any}>{issue.severity}</Badge>
                    <div>
                      <div className="text-[12.5px] leading-snug text-[var(--color-ink-dim)]">{issue.message}</div>
                      {issue.columns?.length > 0 && (
                        <div className="mono mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">
                          columns: {issue.columns.slice(0, 6).join(", ")}
                          {issue.columns.length > 6 ? ` +${issue.columns.length - 6} more` : ""}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>

            <Card title="Cross-dataset linkage" subtitle="Why defect classes cannot be attached to stations from data">
              <div className="grid grid-cols-2 gap-3">
                {(["vision_domain", "simulation_domain"] as const).map((domain) => (
                  <div key={domain} className="panel-flat p-2.5">
                    <div className="panel-title">{domain.replace("_", " ")}</div>
                    <ul className="mt-1.5 space-y-1 text-[11.5px]">
                      {Object.entries(linkage.data?.[domain] ?? {}).map(([key, value]) => (
                        <li key={key} className="flex items-center justify-between gap-2">
                          <span className="text-[var(--color-ink-dim)]">{key.replace(/_/g, " ")}</span>
                          {value ? <Badge tone="ok">yes</Badge> : <Badge tone="muted">no</Badge>}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-ink-dim)]">{linkage.data?.statement}</p>
              <div className="mt-3">
                <Bullets items={(linkage.data?.arguments ?? []).map((a: Record<string, string>) => a.detail)} tone="faint" />
              </div>
              {linkage.data?.arena_costing_present_in_model_but_not_exported?.length ? (
                <div className="mt-3">
                  <div className="panel-title mb-1.5">Present in the Arena models, absent from every export</div>
                  <div className="flex flex-wrap gap-1.5">
                    {linkage.data.arena_costing_present_in_model_but_not_exported.map((name: string) => (
                      <Badge key={name} tone="muted">
                        {name}
                      </Badge>
                    ))}
                  </div>
                </div>
              ) : null}
            </Card>
          </div>

          <Card
            title="Column schema"
            subtitle={quality.data ? `${int(quality.data.rows)} rows · ${quality.data.columns} columns · ${int(quality.data.missing_cells)} missing cells · ${int(quality.data.duplicate_rows)} duplicate rows` : undefined}
            actions={
              <div className="flex items-center gap-2">
                {/* Dynamic active dataset is passed automatically via api.ts */}
                <label className="flex items-center gap-1.5 text-[11.5px] text-[var(--color-ink-dim)]">
                  <input type="checkbox" checked={documentedOnly} onChange={(e) => setDocumentedOnly(e.target.checked)} />
                  documented only
                </label>
              </div>
            }
          >
            {columns.loading ? (
              <Spinner />
            ) : (
              <div className="max-h-[520px] overflow-auto">
                <table className="table">
                  <thead className="sticky top-0 bg-[var(--color-panel)]">
                    <tr>
                      <th>column</th>
                      <th>group</th>
                      <th>count</th>
                      <th>missing</th>
                      <th>mean</th>
                      <th>p05</th>
                      <th>median</th>
                      <th>p95</th>
                      <th>max</th>
                      <th>documented</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(columns.data ?? [])
                      .filter((c) => (documentedOnly ? c.documented : true))
                      .map((c) => (
                        <tr key={c.name}>
                          <td className="mono">{c.name}</td>
                          <td className="text-[11px] text-[var(--color-ink-faint)]">{c.group}</td>
                          <td className="mono">{int(c.count)}</td>
                          <td className={`mono ${c.missing > 0 ? "text-[var(--color-warn)]" : ""}`}>{c.missing || ""}</td>
                          <td className="mono">{num(c.mean ?? NaN, 4)}</td>
                          <td className="mono">{num(c.p05 ?? NaN, 4)}</td>
                          <td className="mono">{num(c.median ?? NaN, 4)}</td>
                          <td className="mono">{num(c.p95 ?? NaN, 4)}</td>
                          <td className="mono">{num(c.maximum ?? NaN, 4)}</td>
                          <td>{c.documented ? <Badge tone="ok">yes</Badge> : <Badge tone="muted">no</Badge>}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
            {quality.data?.undocumented_columns?.length ? (
              <p className="mt-2.5 text-[11.5px] leading-snug text-[var(--color-ink-faint)]">
                {quality.data.undocumented_columns.length} columns are not referenced by any documented station or
                throughput definition: {quality.data.undocumented_columns.slice(0, 8).join(", ")}
                {quality.data.undocumented_columns.length > 8 ? " …" : ""}
              </p>
            ) : null}
          </Card>

          <Card title="MATLAB surrogate design (3000Samplesv3.mat)" subtitle={design.data?.file}>
            {design.data?.present ? (
              <>
                <div className="grid gap-4 lg:grid-cols-2">
                  <div>
                    <div className="panel-title mb-1.5">Variables</div>
                    <div className="mono max-h-[240px] overflow-auto space-y-0.5 text-[11.5px] text-[var(--color-ink-dim)]">
                      {Object.entries(design.data.variables ?? {}).map(([name, meta]: [string, any]) => (
                        <div key={name} className="flex justify-between gap-3">
                          <span>{name}</span>
                          <span className="text-[var(--color-ink-faint)]">
                            {JSON.stringify(meta.shape)} {meta.dtype}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="panel-title mb-1.5">Verified cross-references (exact over all 605,620 rows)</div>
                    <div className="mono space-y-0.5 text-[11.5px] text-[var(--color-ink-dim)]">
                      {Object.entries(design.data.verified_matches ?? {}).map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3">
                          <span>{key}</span>
                          <span className="text-[var(--color-accent)]">{String(value)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-3">
                      <div className="panel-title mb-1.5">Undocumented</div>
                      <Bullets items={design.data.undocumented ?? []} tone="faint" />
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <Empty>{design.data?.error ?? "3000Samplesv3.mat not found in data/raw."}</Empty>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
