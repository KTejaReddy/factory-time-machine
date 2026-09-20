import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Badge, Card, Empty, ErrorBox, FeedbackBar, Spinner } from "../components/ui";
import { api, getActiveDataset, type ExternalImageEntry, type InspectionPrediction } from "../lib/api";
import { pct, useApi } from "../lib/hooks";

const GRID = 8;

function AttentionOverlay({ prediction }: { prediction: InspectionPrediction }) {
  const cells = Array.from({ length: GRID * GRID }, (_, i) => {
    const gx = i % GRID;
    const gy = Math.floor(i / GRID);
    const match = prediction.attention.find((a) => Math.round(a.x * GRID) === gx && Math.round(a.y * GRID) === gy);
    return { gx, gy, weight: match?.weight ?? 0 };
  });
  return (
    <div className="pointer-events-none absolute inset-0">
      <div className="grid h-full w-full" style={{ gridTemplateColumns: `repeat(${GRID}, 1fr)`, gridTemplateRows: `repeat(${GRID}, 1fr)` }}>
        {cells.map((cell) => (
          <div
            key={`${cell.gx}-${cell.gy}`}
            style={{ background: cell.weight > 0 ? `rgba(251,113,133,${0.12 + cell.weight * 0.45})` : "transparent" }}
          />
        ))}
      </div>
    </div>
  );
}

function ResultPanel({
  prediction,
  imageUrl,
  external,
}: {
  prediction: InspectionPrediction;
  imageUrl: string;
  external?: boolean;
}) {
  const [showAttention, setShowAttention] = useState(true);
  return (
    <div className="flex flex-col md:flex-row gap-6">
      {/* LEFT: Image */}
      <div className="w-full md:w-1/2 flex flex-col items-center">
        <div className="mb-3 w-full flex justify-between items-center px-1">
          <span className="text-[13px] font-semibold text-[var(--color-ink)]">Product Image</span>
          <label className="flex items-center gap-1.5 text-[11.5px] text-[var(--color-ink-dim)]">
            <input type="checkbox" checked={showAttention} onChange={(e) => setShowAttention(e.target.checked)} className="accent-[var(--color-accent)]" />
            Grad-CAM Overlay
          </label>
        </div>
        <div className="relative w-full aspect-square overflow-hidden rounded-md border border-[var(--color-edge)] bg-black shadow-sm">
          <img src={imageUrl} alt={prediction.specimen} className="block h-full w-full object-cover" />
          {showAttention && <AttentionOverlay prediction={prediction} />}
        </div>
        {showAttention && (
          <p className="mt-3 text-[11.5px] text-center text-[var(--color-ink-faint)] max-w-xs leading-relaxed">
            Highlighted area indicates where the AI focused. This is model attention, not a localized ground truth.
          </p>
        )}
      </div>

      {/* RIGHT: Details */}
      <div className="w-full md:w-1/2 space-y-5">
        <div className="flex items-center gap-3 border-b border-[var(--color-edge)] pb-3">
          <div className="text-[20px] font-bold tracking-tight">
            {prediction.verdict === "OOD / Do Not Trust" ? (
              <span className="text-[var(--color-warn)]">⚠️ Low Trust Prediction</span>
            ) : prediction.verdict === "Uncertain" ? (
              <span className="text-[var(--color-warn)]">⚠️ Uncertain</span>
            ) : prediction.defect ? (
              <span className="text-[var(--color-bad)]">❌ Defect Detected</span>
            ) : (
              <span className="text-[var(--color-ok)]">✅ Normal</span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="panel-flat p-3">
            <div className="text-[11.5px] text-[var(--color-ink-faint)] uppercase tracking-wider mb-1">Prediction</div>
            <div className="text-[16px] font-semibold text-[var(--color-ink)]">{prediction.label_display}</div>
          </div>
          <div className="panel-flat p-3">
            <div className="text-[11.5px] text-[var(--color-ink-faint)] uppercase tracking-wider mb-1">Model Confidence</div>
            <div className={`text-[16px] font-semibold ${prediction.confidence < 0.6 ? 'text-[var(--color-warn)]' : 'text-[var(--color-accent)]'}`}>
              {pct(prediction.confidence * 100, 1)}
            </div>
          </div>
          <div className="panel-flat p-3">
            <div className="text-[11.5px] text-[var(--color-ink-faint)] uppercase tracking-wider mb-1">TTA Stability</div>
            <div className={`text-[14px] font-medium ${prediction.tta_agreement < 0.85 ? 'text-[var(--color-warn)]' : 'text-[var(--color-ink-dim)]'}`}>
              {pct(prediction.tta_agreement * 100, 0)}
            </div>
          </div>
          <div className="panel-flat p-3">
            <div className="text-[11.5px] text-[var(--color-ink-faint)] uppercase tracking-wider mb-1">Distribution Check</div>
            <div className={`text-[13px] font-medium leading-snug ${prediction.out_of_distribution ? 'text-[var(--color-warn)]' : 'text-[var(--color-ok)]'}`}>
              {prediction.out_of_distribution ? "Outside training distribution" : "In distribution"}
            </div>
          </div>
        </div>

        {prediction.verdict !== "Confident" && prediction.uncertainty_reason && (
          <div className="callout callout-warn text-[12.5px]">
            <h4 className="font-bold mb-1">Why</h4>
            <p className="leading-relaxed">{prediction.uncertainty_reason}</p>
          </div>
        )}

        {external && (
          <div className="callout callout-info text-[12px]">
            🧪 <strong>External Test Image</strong> Not used for training. Treat as a robustness probe.
          </div>
        )}

        <details className="cursor-pointer text-[12.5px] text-[var(--color-ink-dim)] bg-[var(--color-hull)] p-3 rounded-md">
          <summary className="font-semibold text-[var(--color-ink)] outline-none">How this works</summary>
          <div className="mt-3 space-y-2.5 leading-relaxed">
            <p><strong>CNN:</strong> Trained on 12,000 factory images (crack, hole, normal, rust, scratch).</p>
            <p><strong>TTA:</strong> Test-time augmentation. Averages prediction with flipped image to check stability.</p>
            <p><strong>Grad-CAM:</strong> Visualizes the pixels that most influenced the prediction.</p>
            <p><strong>OOD Check:</strong> Mahalanobis distance in feature space prevents confident errors on weird images.</p>
          </div>
        </details>
      </div>
    </div>
  );
}

export default function Inspection() {
  const location = useLocation();
  const overview = useApi(() => api.overview(), []);
  const activeDataset = getActiveDataset();
  const datasetCapabilities = overview.data?.datasets.find((d: any) => d.key === activeDataset)?.capabilities?.available;
  const isVisionSupported = datasetCapabilities?.vision ?? true;

  const samples = useApi(() => api.inspectSamples(8), []);
  const externalInfo = useApi(() => api.externalInfo(), []);
  const externalList = useApi(() => api.externalList(undefined, 40), []);

  const [tab, setTab] = useState<"training" | "external">("training");
  const [specimen, setSpecimen] = useState<string | null>(null);
  const [prediction, setPrediction] = useState<InspectionPrediction | null>(null);
  const [isExternal, setIsExternal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genKind, setGenKind] = useState<"mixed" | "variation" | "nonfactory">("mixed");

  useEffect(() => {
    // If navigated from Dashboard with an external ID
    if (location.state?.externalId && !isExternal) {
      setTab("external");
      runExternal(location.state.externalId);
      // clear state so it doesn't re-trigger
      window.history.replaceState({}, document.title);
      return;
    }

    if (!specimen && samples.data?.samples && tab === "training") {
      const first = Object.values(samples.data.samples)[0]?.[0];
      if (first) setSpecimen(first);
    }
  }, [location.state, samples.data, specimen, tab]);

  const runInspection = async (name: string) => {
    setSpecimen(name);
    setIsExternal(false);
    setLoading(true);
    setError(null);
    try {
      setPrediction(await api.predict(name));
    } catch (err) {
      setPrediction(null);
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const runExternal = async (id: string) => {
    setIsExternal(true);
    setLoading(true);
    setError(null);
    try {
      setPrediction(await api.externalPredict(id));
    } catch (err) {
      setPrediction(null);
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const upload = async (file: File | undefined | null) => {
    if (!file) return;
    // Refuse locally what the server would refuse anyway, so the user is not made
    // to wait out a doomed upload just to read a size error afterwards.
    const limit = externalInfo.data?.max_upload_bytes;
    if (limit && file.size > limit) {
      setError(
        `This image is ${(file.size / (1024 * 1024)).toFixed(1)} MB, above the ${Math.floor(limit / (1024 * 1024))} MB limit. ` +
          "Please pick a smaller file (or resize it first).",
      );
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const entry = await api.externalUpload(file, uploadNote);
      setUploadNote("");
      await externalList.reload();
      await externalInfo.reload();
      await runExternal(entry.id);
      setTab("external");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      await api.externalGenerate(genKind, 6);
      await externalList.reload();
      await externalInfo.reload();
      setTab("external");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.externalDelete(id);
      if (isExternal && prediction?.external?.id === id) {
        setPrediction(null);
      }
      await externalList.reload();
      await externalInfo.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const entries = externalList.data ?? [];

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-[24px] font-bold tracking-tight">Inspection</h1>
        <p className="mt-2 text-[14px] text-[var(--color-ink-dim)]">
          Inspect a product image for defects, backed by AI analysis.
        </p>
      </header>

      <div className="flex gap-2">
        <button className={`btn ${tab === "training" ? "btn-primary" : ""}`} onClick={() => setTab("training")}>
          📁 Factory images
        </button>
        <button className={`btn ${tab === "external" ? "btn-primary" : ""}`} onClick={() => setTab("external")}>
          🧪 Test with a new image
        </button>
      </div>

      {tab === "training" ? (
        <div className="grid gap-6">
          <Card title="1. Select Image">
            {!isVisionSupported ? (
              <Empty>No inspection images were found in this dataset.</Empty>
            ) : samples.loading ? (
              <Spinner />
            ) : (
              <div className="flex flex-wrap gap-2">
                {Object.entries(samples.data?.samples ?? {}).map(([_, names]) =>
                  names.map((name) => (
                    <button
                      key={name}
                      onClick={() => runInspection(name)}
                      title={name}
                      className={`h-[64px] w-[64px] overflow-hidden rounded border-2 transition-all ${
                        specimen === name && !isExternal ? "border-[var(--color-accent)] ring-2 ring-[var(--color-accent-soft)]" : "border-[var(--color-edge)] hover:border-[var(--color-accent-edge)]"
                      }`}
                    >
                      <img src={api.thumbnailUrl(name, 128)} alt={name} loading="lazy" className="h-full w-full object-cover" />
                    </button>
                  )),
                )}
              </div>
            )}
            <p className="mt-3 text-[11.5px] text-[var(--color-ink-faint)]">
              These images come from the training archive.
            </p>
          </Card>

          <Card title="2. Inspection Result">
            {loading && !isExternal ? (
              <Spinner label="Analyzing image..." />
            ) : error && !isExternal ? (
              <ErrorBox message={error} onRetry={() => specimen && runInspection(specimen)} />
            ) : !prediction || isExternal ? (
              <Empty>Select an image to check.</Empty>
            ) : (
              <ResultPanel prediction={prediction} imageUrl={api.imageUrl(prediction.specimen)} />
            )}
          </Card>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="callout callout-info border-dashed">
            🧪 <strong>External Test Image — Not Used for Training.</strong> Everything on this tab is stored outside
            the training archive (in <span className="mono">data/external_test/</span>) and is never added to the
            model's training data. Use it to see how the AI behaves on images it has never seen — including images
            that are not factory parts at all.
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <Card title="1. Upload or Generate a Test Image">
              <div className="space-y-4">
                <div>
                  <label className="mb-1 block text-[12px] font-semibold text-[var(--color-ink-faint)]">
                    Upload your own image (PNG or JPEG):
                  </label>
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    className="field"
                    onChange={(e) => upload(e.target.files?.[0])}
                    disabled={uploading}
                  />
                  {uploading && <Spinner label="Uploading..." />}
                </div>
                <div>
                  <label className="mb-1 block text-[12px] font-semibold text-[var(--color-ink-faint)]">
                    Optional note (stored with the image):
                  </label>
                  <input
                    className="field"
                    placeholder="e.g. photo from the line, random web image…"
                    value={uploadNote}
                    onChange={(e) => setUploadNote(e.target.value)}
                  />
                </div>

                <div className="border-t border-[var(--color-edge)] pt-3">
                  <label className="mb-1 block text-[12px] font-semibold text-[var(--color-ink-faint)]">
                    …or generate synthetic test images:
                  </label>
                  <div className="flex gap-2">
                    <select className="field" value={genKind} onChange={(e) => setGenKind(e.target.value as typeof genKind)}>
                      <option value="mixed">Mixed (both kinds)</option>
                      <option value="variation">Twisted factory images</option>
                      <option value="nonfactory">Non-factory images</option>
                    </select>
                    <button className="btn" onClick={generate} disabled={generating}>
                      {generating ? "Generating…" : "Generate 6"}
                    </button>
                  </div>
                  <p className="mt-2 text-[11.5px] text-[var(--color-ink-faint)]">
                    “Twisted factory images” are real archive images with brightness, rotation, flip, blur, noise or
                    contrast changes. “Non-factory images” are synthetic faces, cars, trees, phones and random noise
                    that look nothing like the training data. These are robustness probes only — not real factory
                    images and not labelled ground truth.
                  </p>
                </div>
              </div>
            </Card>

            <Card title="2. Pick a Test Image" subtitle={externalInfo.data ? `${externalInfo.data.count} stored test image(s)` : undefined}>
              {externalList.loading ? (
                <Spinner />
              ) : entries.length === 0 ? (
                <Empty>Nothing here yet. Upload an image or generate a set above.</Empty>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {entries.map((entry: ExternalImageEntry) => (
                    <div key={entry.id} className="relative">
                      <button
                        onClick={() => runExternal(entry.id)}
                        title={`${entry.source === "upload" ? `upload: ${entry.original_name}` : entry.description || "generated"}${entry.note ? ` — ${entry.note}` : ""}`}
                        className={`h-[64px] w-[64px] overflow-hidden rounded border-2 ${
                          prediction?.external?.id === entry.id ? "border-[var(--color-accent)]" : "border-[var(--color-edge)]"
                        }`}
                      >
                        <img src={api.externalImageUrl(entry.id)} alt={entry.original_name} loading="lazy" className="h-full w-full object-cover" />
                      </button>
                      <button
                        className="absolute -right-1.5 -top-1.5 grid h-[18px] w-[18px] place-items-center rounded-full border border-[var(--color-edge)] bg-[var(--color-void)] text-[10px] text-[var(--color-ink-faint)] hover:text-[var(--color-bad)]"
                        title="Delete this test image"
                        onClick={() => remove(entry.id)}
                      >
                        ✕
                      </button>
                      <div className="mt-0.5 text-center text-[9.5px] uppercase tracking-wide text-[var(--color-ink-faint)]">
                        {entry.source === "upload" ? "upload" : "generated"}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="mt-3 flex items-center gap-2 text-[11px] text-[var(--color-ink-faint)]">
                <Badge tone="assumed">test-only</Badge>
                Never used for training · deleted images are removed permanently
              </div>
            </Card>
          </div>

          <Card title="3. Result">
            {loading && isExternal ? (
              <Spinner label="AI is checking..." />
            ) : error && isExternal ? (
              <ErrorBox message={error} />
            ) : !prediction?.external || !isExternal ? (
              <Empty>Pick a test image to see the result here.</Empty>
            ) : (
              <ResultPanel
                prediction={prediction}
                imageUrl={api.externalImageUrl(prediction.external.id)}
                external
              />
            )}
          </Card>

          {isExternal && prediction?.external && (
            <Card title="👨‍🔧 Review">
              <FeedbackBar
                findingId={`external-inspection:${prediction.external.id}`}
                findingKind="external_inspection"
                findingTitle={`External image → ${prediction.label} (${pct(prediction.confidence * 100, 1)})`}
                payload={{
                  external_id: prediction.external.id,
                  source: prediction.external.source,
                  label: prediction.label,
                  confidence: prediction.confidence,
                  uncertain: prediction.uncertain,
                }}
              />
            </Card>
          )}
        </div>
      )}

      {tab === "training" && prediction && !isExternal && (
        <Card title="👨‍🔧 Review">
          <FeedbackBar
            findingId={`inspection:${prediction.specimen}`}
            findingKind="inspection"
            findingTitle={`${prediction.specimen} → ${prediction.label} (${pct(prediction.confidence * 100, 1)})`}
            payload={{
              specimen: prediction.specimen,
              label: prediction.label,
              confidence: prediction.confidence,
              uncertain: prediction.uncertain,
            }}
          />
        </Card>
      )}
    </div>
  );
}
