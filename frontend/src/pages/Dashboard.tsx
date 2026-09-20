import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Card, Empty, ErrorBox, Spinner } from "../components/ui";
import {
  api,
  getActiveDataset,
  notifyWorkspaceChanged,
  setActiveDataset,
  type AnalysisSummaryCard,
  type DatasetRecord,
  type UploadResult,
} from "../lib/api";
import { int, pct, useApi } from "../lib/hooks";
import { CapabilityChips, DatasetIdentity, RunAnalysisButton, statusTone } from "../components/workspace";
import { useWorkspaceDatasets } from "../components/Layout";

export default function Dashboard() {
  const navigate = useNavigate();
  const overview = useApi(() => api.overview(), []);
  const production = useApi(() => api.productionSnapshot(), []);
  const anomalies = useApi(() => api.anomalies(undefined, 5), []);
  const { records, reload: reloadRegistry } = useWorkspaceDatasets();
  const activeKey = getActiveDataset();
  const [summary, setSummary] = useState<AnalysisSummaryCard | null>(null);

  const [files, setFiles] = useState<File[]>([]);
  const [inputNonce, setInputNonce] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);

  const [imageUploading, setImageUploading] = useState(false);
  const [imageUploadError, setImageUploadError] = useState<string | null>(null);

  const active: DatasetRecord | undefined = records.find((r) => r.key === activeKey);

  useEffect(() => {
    if (!activeKey) {
      setSummary(null);
      return;
    }
    api.analysisSummary(activeKey).then(setSummary).catch(() => setSummary(null));
  }, [activeKey, uploadResult]);

  const doUpload = async () => {
    if (!files.length || uploading) return;
    setUploading(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const result = await api.uploadDataset(files);
      setUploadResult(result);
      // A new upload opens its own workspace and becomes the active dataset; the
      // previous dataset keeps its case file (nothing is replaced).
      if (result.uploaded.length > 0) {
        setActiveDataset(result.uploaded[0].key);
        notifyWorkspaceChanged();
        reloadRegistry();
      }
      setFiles([]);
      setInputNonce((n) => n + 1);
      overview.reload();
    } catch (err) {
      setUploadError((err as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const doImageUpload = async (file: File) => {
    if (!file || imageUploading) return;
    setImageUploading(true);
    setImageUploadError(null);
    try {
      const entry = await api.externalUpload(file, "Dashboard Upload");
      navigate("/inspection", { state: { externalId: entry.id } });
    } catch (err) {
      setImageUploadError((err as Error).message);
      setInputNonce((n) => n + 1);
    } finally {
      setImageUploading(false);
    }
  };

  if (overview.loading && !overview.data) return <Spinner label="Loading..." />;
  if (overview.error) return <ErrorBox message={overview.error} onRetry={overview.reload} />;

  const snap = production.data;
  const top = snap?.bottleneck_candidates?.[0];
  const topAnomaly = anomalies.data?.top?.[0];
  // The saved run's own headline cards. Reading them from the summary keeps the
  // Overview honest: a supported feature is never labelled "not supported", and a
  // disabled one carries the reason the backend recorded.
  const highlights = summary?.status === "complete" ? summary.highlights : undefined;
  const ready = summary?.status === "complete";

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <section className="pt-6 text-center fade-in">
        <h1 className="text-[28px] font-bold tracking-tight text-[var(--color-ink)]">Factory Time Machine</h1>
        <p className="mt-3 text-[15px] text-[var(--color-ink-dim)]">
          Every manufacturing dataset gets its own intelligent case file.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Link className="btn px-5 py-2.5 text-[14px]" to="/datasets">
            Your datasets
          </Link>
          <Link className="btn px-5 py-2.5 text-[14px]" to="/investigator">
            Ask AI
          </Link>
        </div>
      </section>

      <div className="mt-8 grid gap-6 md:grid-cols-2">
        <section className="panel flex flex-col items-center justify-center border border-[var(--color-edge)] bg-white p-8 shadow-sm">
          <div className="mb-2 text-[32px]">📊</div>
          <h2 className="mb-1 text-[16px] font-bold uppercase tracking-wider text-[var(--color-ink)]">Analyze a Dataset</h2>
          <p className="mb-6 text-[13px] text-[var(--color-ink-dim)]">CSV · one dataset per case file</p>
          <div className="flex w-full max-w-sm flex-col items-center">
            <input
              key={`ds-${inputNonce}`}
              type="file"
              accept=".csv"
              multiple
              aria-label="Dataset files"
              className="field w-full text-center text-[13px]"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            <button className="btn btn-primary mt-3 w-full justify-center py-2 text-[14px]" disabled={!files.length || uploading} onClick={doUpload}>
              {uploading ? "Uploading..." : "Upload Dataset"}
            </button>
            {uploadError && (
              <div className="mt-3 w-full">
                <ErrorBox message={uploadError} />
              </div>
            )}
            {uploadResult && uploadResult.uploaded[0] && (
              <div className="mt-3 w-full rounded-md border border-[var(--color-ok-edge)] bg-[var(--color-ok-soft)] p-3">
                <div className="eyebrow text-[var(--color-ok)]">Dataset ready</div>
                <div className="mt-1 text-[14px] font-semibold text-[var(--color-ink)]">{uploadResult.uploaded[0].label}</div>
                <div className="mono mt-0.5 text-[10.5px] text-[var(--color-ink-faint)]">{uploadResult.uploaded[0].dataset_id}</div>
                <div className="mt-2 text-[12px] text-[var(--color-ink-dim)]">
                  {int(uploadResult.uploaded[0].rows)} rows × {uploadResult.uploaded[0].columns} columns
                  {uploadResult.uploaded[0].capabilities?.detected && (
                    <>
                      {" "}· detected:{" "}
                      {(uploadResult.uploaded[0].capabilities.detected.station_fields ?? []).length} station field(s),{" "}
                      {(uploadResult.uploaded[0].capabilities.detected.process_fields ?? []).length} process column(s),{" "}
                      {(uploadResult.uploaded[0].capabilities.detected.cost_fields ?? []).length} cost column(s)
                    </>
                  )}
                </div>
                <div className="mt-1">
                  <CapabilityChips caps={uploadResult.uploaded[0].capabilities} compact />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    className="btn btn-primary"
                    onClick={() => {
                      const id = uploadResult.uploaded[0].dataset_id;
                      if (id) navigate(`/dataset/${id}`);
                      else navigate("/datasets");
                    }}
                  >
                    Start analysis
                  </button>
                  <Link className="btn" to="/datasets">
                    All datasets
                  </Link>
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="panel flex flex-col items-center justify-center border border-[var(--color-edge)] bg-white p-8 shadow-sm">
          <div className="mb-2 text-[32px]">👁️</div>
          <h2 className="mb-1 text-[16px] font-bold uppercase tracking-wider text-[var(--color-ink)]">Inspect an Image</h2>
          <p className="mb-6 text-[13px] text-[var(--color-ink-dim)]">PNG · JPG · test images only</p>
          <div className="flex w-full max-w-sm flex-col items-center">
            <input
              key={`img-${inputNonce}`}
              type="file"
              accept="image/png,image/jpeg,image/jpg"
              aria-label="Image files"
              className="field w-full text-center text-[13px]"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) doImageUpload(f);
              }}
            />
            <div className="mt-3 w-full text-center text-[11.5px] text-[var(--color-ink-faint)]">
              External images are stored separately from the dataset and are never used for training.
            </div>
            {imageUploading && (
              <div className="mt-3 w-full text-center text-[13px] text-[var(--color-ink-dim)]">
                <Spinner label="Uploading image..." />
              </div>
            )}
            {imageUploadError && (
              <div className="mt-3 w-full">
                <ErrorBox message={imageUploadError} />
              </div>
            )}
          </div>
        </section>
      </div>

      {!active ? (
        <Empty>No dataset is open. Upload a CSV above, or open one from your datasets.</Empty>
      ) : (
        <>
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="eyebrow">Active case file</h3>
              <div className="flex items-center gap-2">
                <Badge tone={statusTone(active.status)}>{active.status_label}</Badge>
                <Link className="btn" to={`/dataset/${active.id}`}>
                  Open workspace
                </Link>
                <RunAnalysisButton
                  label={ready ? "Re-run analysis" : "Run analysis"}
                  onDone={() => {
                    api.analysisSummary(activeKey).then(setSummary).catch(() => undefined);
                    reloadRegistry();
                  }}
                />
              </div>
            </div>
            <DatasetIdentity record={active} />
            <CapabilityChips caps={active.capabilities} compact />
          </section>

          <section className="space-y-3">
            <h3 className="eyebrow">What was found</h3>
            {ready ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Card title="Quality" actions={<Badge tone="muted">dataset</Badge>}>
                  <p className="text-[13px] leading-snug text-[var(--color-ink-dim)]">{highlights?.quality?.rows != null ? `${int(highlights.quality.rows)} rows · ${int(highlights.quality.missing_cells)} missing cells · ${int(highlights.quality.duplicate_rows)} duplicates` : summary?.main_issue}</p>
                  <p className="mt-2 text-[12px] leading-snug text-[var(--color-ink-dim)]">{summary?.main_issue}</p>
                </Card>
                <Card title="Process" actions={<Badge tone={highlights?.process?.available ? "ok" : "warn"}>anomaly model</Badge>}>
                  <p className="text-[13px] leading-snug text-[var(--color-ink-dim)]">
                    {highlights?.process?.available
                      ? `${pct((highlights.process.anomalous_fraction ?? 0) * 100, 2)} of scored rows beyond the threshold`
                      : highlights?.process?.reason ?? "not supported by this dataset"}
                  </p>
                  <Link className="btn mt-3 text-[12px]" to="/forensic">
                    Investigate
                  </Link>
                </Card>
                <Card title="Production" actions={<Badge tone={highlights?.production?.available ? "ok" : "warn"}>constraint</Badge>}>
                  <p className="text-[13px] leading-snug text-[var(--color-ink-dim)]">
                    {highlights?.production?.bottleneck?.label
                      ? `${highlights.production.bottleneck.label} (utilisation ${pct((highlights.production.bottleneck.utilisation ?? 0) * 100, 1)})`
                      : top?.label
                      ? `${top.label} (utilisation ${pct((top.utilization ?? 0) * 100, 1)})`
                      : highlights?.production?.reason ?? "—"}
                  </p>
                  <Link className="btn mt-3 text-[12px]" to="/production">
                    View Production
                  </Link>
                </Card>
                <Card
                  title="Economics & repairs"
                  actions={
                    <Badge tone={highlights?.economics?.available ? "ok" : "warn"}>
                      {highlights?.economics?.available ? "computed" : "rate card required"}
                    </Badge>
                  }
                >
                  <p className="text-[13px] leading-snug text-[var(--color-ink-dim)]">{summary?.repair ?? "No supported repair yet."}</p>
                  {!highlights?.economics?.available && highlights?.economics?.reason ? (
                    <p className="mt-2 text-[12px] leading-snug text-[var(--color-ink-dim)]">{highlights.economics.reason}</p>
                  ) : null}
                  <div className="mt-3 flex gap-2">
                    <Link className="btn text-[12px]" to="/repairs">
                      Repairs
                    </Link>
                    <Link className="btn text-[12px]" to="/economics">
                      Rate card
                    </Link>
                  </div>
                </Card>
              </div>
            ) : (
              <Card title="No saved analysis for this dataset yet" subtitle="Run the analysis to store its own results">
                <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-dim)]">
                  Until then this page shows the live measurements only. Memory of a previous dataset's analysis never
                  appears here: {active.name} has its own case file.
                </p>
                {top && (
                  <p className="mt-2 text-[12.5px] text-[var(--color-ink-dim)]">
                    Live measurement: busiest station {top.label} at {pct((top.utilization ?? 0) * 100, 1)}.
                    {topAnomaly ? ` Row ${int(topAnomaly.run_index)} scores as an outlier.` : ""}
                  </p>
                )}
              </Card>
            )}
          </section>
        </>
      )}
    </div>
  );
}
