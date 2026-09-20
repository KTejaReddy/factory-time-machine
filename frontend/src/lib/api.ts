/** Typed API client. Every request goes through `request()` so errors stay uniform. */

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/**
 * Turn an error response into one sentence a non-engineer can read.
 *
 * The backend's own messages (a string `detail`) are already written for people
 * and are used as-is. A FastAPI validation failure arrives as an array of field
 * problems instead, and rendering that array verbatim used to show users raw JSON,
 * so it is summarised field by field.
 */
function describeError(status: number, statusText: string, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();

  if (Array.isArray(detail) && detail.length) {
    const problems = detail.slice(0, 3).map((item) => {
      const entry = item as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(entry.loc)
        ? entry.loc.filter((part) => typeof part === "string" && part !== "body" && part !== "query").join(".")
        : "";
      const message = (entry.msg ?? "is not valid").replace(/^Input should be/i, "must be one of");
      return field ? `${field}: ${message}` : message;
    });
    const more = detail.length > problems.length ? ` (+${detail.length - problems.length} more)` : "";
    return `That request was not accepted. ${problems.join("; ")}${more}.`.slice(0, 400);
  }

  if (status === 404) return "That item was not found — it may have been removed.";
  if (status === 413) return "That file is too large to accept.";
  if (status === 429) return "Too many requests just now. Please wait a moment and try again.";
  if (status === 503) return "The service is starting up or temporarily unavailable. Please try again shortly.";
  if (status >= 500) return "The server hit a problem while handling that. The details are in the backend log.";
  return `${status} ${statusText}`.trim();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // If the path contains ?key= and nothing else, or ends with ?key=, it means an empty dataset key was passed.
  // We can short-circuit the network request to avoid console errors.
  if (path.includes("key=") && path.match(/key=(&|$)/)) {
    throw new ApiError("No dataset loaded. Please upload a dataset to begin.", 400);
  }

  // A FormData body must carry its own multipart boundary: setting a JSON
  // content type on it makes the server reject the request.
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...(isFormData ? {} : { headers: { "Content-Type": "application/json" } }),
      ...init,
    });
  } catch {
    // A dropped connection (backend stopped, laptop offline) must read like a
    // sentence, not like a browser TypeError.
    throw new ApiError("Could not reach the analysis service. Check that the backend is running.", 0);
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* no JSON body: fall back to the status */
    }
    throw new ApiError(describeError(response.status, response.statusText, body), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
};

// ---------------------------------------------------------------- types
export interface ProcessingStatus {
  state: "idle" | "running" | "ready" | "failed";
  progress: number;
  step: string;
  message: string;
  started_at?: string | null;
  finished_at?: string | null;
  seconds?: number | null;
  stages?: Record<string, number>;
  error?: string | null;
}

export interface DatasetSummary {
  key: string;
  label: string;
  kind: "images" | "simulation" | "surrogate_design";
  present: boolean;
  rows: number;
  columns: number;
  source_file: string;
  description: string;
  warnings: string[];
  extras: Record<string, any>;
  /** What this dataset can support, plus the reason each switched-off feature is off. */
  capabilities?: CapabilityMap;
}

/** One shape for every dataset: what is available, and why the rest is not. */
export interface CapabilityMap {
  available?: Record<string, boolean>;
  reasons?: Record<string, string>;
  detected?: {
    station_fields?: string[];
    process_fields?: string[];
    cost_fields?: string[];
    batch_ids?: boolean;
  };
}

export interface UploadedDataset {
  key: string;
  dataset_id?: string | null;
  status?: string;
  status_label?: string;
  uploaded_at?: string | null;
  label: string;
  rows: number;
  columns: number;
  capabilities: CapabilityMap;
  next?: { open_workspace: string; run_analysis: string };
}

/** One dataset's case file: identity, provenance and what it has saved. */
export interface DatasetRecord {
  id: string;
  dataset_id: string;
  key: string;
  name: string;
  kind: string;
  source_file: string;
  uploaded_at: string | null;
  status: string;
  status_label: string;
  rows: number;
  columns: number;
  present: boolean;
  notes: string;
  profile: Record<string, any>;
  capabilities: CapabilityMap;
  artifacts: { scenarios: number; feedback: number; analyses: number; has_rate_card: boolean };
}

export interface AnalysisSummaryCard {
  status: "complete" | "not_run";
  dataset?: { id: string; dataset_id: string; key: string; name: string; uploaded_at?: string | null; status?: string; rows?: number; columns?: number };
  generated_at?: string;
  analysis_run_id?: number;
  main_issue?: string;
  repair?: string | null;
  estimated_impact?: string | null;
  estimated_impact_value?: number | null;
  currency?: string | null;
  confidence?: { score?: number; basis?: string[] };
  formats?: Array<{ format: string; label: string; url: string }>;
  limitations?: string[];
}

export interface RepairCandidate {
  repair: string;
  kind: string;
  target: Record<string, any>;
  measured: Record<string, any>;
  expected_quality_effect: string;
  expected_throughput_effect: Record<string, any>;
  expected_loss_reduction: { amount?: number | null; per_row_amount?: number | null; observations?: number; currency?: string; lines?: Array<Record<string, any>>; reason?: string };
  intervention_cost: { amount?: number | null; basis?: string; reason?: string };
  net_impact: { amount?: number | null; currency?: string | null };
  confidence: "high" | "medium" | "low";
  evidence: string[];
  assumptions: string[];
  scenario?: Record<string, any> | null;
}

export interface RepairReport {
  dataset_key: string;
  status: "available" | "cost_comparison_unavailable" | "no_positive_net_impact" | "unavailable";
  statement: string;
  currency: string;
  currency_source: string;
  rate_card_used: boolean;
  rates: Record<string, { value: number; source: string }>;
  dataset_cost_columns: Record<string, { column: string; mean: number }>;
  cheapest_supported_effective_repair: (RepairCandidate & { benefit_cost_ratio?: number; selection_reason?: string }) | null;
  candidates: RepairCandidate[];
  reductions_assumed: Record<string, number>;
  limitations: string[];
}

export interface SavedAnalysis {
  status: "saved" | "not_run";
  id?: number;
  dataset_key?: string;
  dataset_id?: string;
  created_at?: string;
  seconds?: number;
  payload?: Record<string, any>;
  reason?: string;
  key?: string;
}

export interface UploadResult {
  message: string;
  files: string[];
  uploaded: UploadedDataset[];
  status: ProcessingStatus;
}

export interface DatasetIssue {
  severity: "info" | "warning" | "error";
  message: string;
  rows_affected: number;
  columns: string[];
}

export interface Overview {
  status: ProcessingStatus;
  datasets: DatasetSummary[];
  issues: DatasetIssue[];
  linkage: Record<string, any>;
  capabilities: Record<string, any> & { reasons: Record<string, string> };
  provenance: Record<string, any>;
}

export interface ColumnProfile {
  name: string;
  group: string;
  dtype: string;
  count: number;
  missing: number;
  mean?: number | null;
  std?: number | null;
  minimum?: number | null;
  p05?: number | null;
  median?: number | null;
  p95?: number | null;
  maximum?: number | null;
  documented: boolean;
  note: string;
}

export interface StationRow {
  station: string;
  label: string;
  stage: string;
  units: string;
  capacity?: number | null;
  capacity_source?: string;
  processing_time_seconds?: number | null;
  time_note?: string;
  utilization?: number | null;
  utilization_source?: string;
  utilization_column?: string;
  queue_mean?: number | null;
  queue_max?: number | null;
  queue_columns?: string[];
  wip?: number | null;
  wip_evidence?: number | null;
  wip_evidence_source?: string | null;
  wip_evidence_note?: string;
  processed_units_mean?: number | null;
  headroom?: number | null;
  bottleneck_score?: number | null;
  utilisation_normalised?: number;
  rank?: number | null;
  score_components?: Record<string, number>;
  evidence?: string[];
  unavailable?: string[];
}

export interface ProductionSnapshot {
  model_key: string;
  model_label: string;
  horizon_seconds: number;
  throughput_total?: number | null;
  throughput_unit: string;
  wip_total?: number | null;
  wip_definition: string;
  stations: StationRow[];
  bottleneck_candidates: StationRow[];
  ranking_method: string;
  ranking_weights: Record<string, number>;
  utilisation_reference_max?: number;
  headroom: {
    stations: Array<Record<string, any>>;
    first_station_to_saturate?: string | null;
    plant_headroom_pct?: number | null;
    note: string;
  };
  calibration: CalibrationReport;
  utilisation_profile: Array<Record<string, any>>;
  unavailable: string[];
}

export interface CalibrationReport {
  checked: boolean;
  reason?: string;
  horizon_seconds?: number;
  horizon_source?: string;
  sample_rows?: number;
  rows?: Array<Record<string, any>>;
  verifiable_stations?: number;
  capacity_consistent_stations?: number;
  max_abs_residual_pct?: number | null;
  mean_abs_residual_pct?: number | null;
  verdict: string;
  implied_capacities?: Array<Record<string, any>>;
  limitations?: string[];
}

export interface AnomalyReport {
  model_key: string;
  /** "unavailable" carries a ``reason`` instead of numbers - never a silent zero. */
  status?: "success" | "unavailable";
  reason?: string;
  n_rows: number;
  n_scored: number;
  threshold: number;
  anomalous_fraction: number;
  anomalous_runs: number;
  top: Array<{
    run_index: number;
    score: number;
    parts?: number | null;
    drivers: Array<{ feature: string; z: number; value?: number | null; direction: string; baseline_mean?: number }>;
  }>;
  feature_importance: Array<{ feature: string; loading: number }>;
  method: string;
  limitations: string[];
}

export interface ForensicCase {
  case_id: string;
  label: string;
  scope: string;
  model_key: string;
  model_label: string;
  statistic: string;
  baseline_definition: string;
  selection?: Record<string, any>;
  outcome: Record<string, any>;
  timeline: Array<{
    station: string;
    label: string;
    stage: string;
    metric: string;
    value?: number | null;
    baseline?: number | null;
    z?: number | null;
    significance?: number | null;
    delta_pct?: number | null;
    severity: "low" | "moderate" | "high";
    capacity?: number | null;
    evidence: string[];
  }>;
  first_divergence?: ForensicCase["timeline"][number] | null;
  co_occurring: ForensicCase["timeline"];
  evidence: Array<{ claim: string; value?: number | null; unit?: string; source: string; strength: string; significance?: number }>;
  confidence: number;
  confidence_basis: string[];
  limitations: string[];
  data_quality: string[];
  causal_caveat: string;
}

export interface PropNode {
  id: string;
  label: string;
  kind: "process" | "state" | "defect" | "outcome" | "economic";
  domain: string;
  status: "observed" | "assumed" | "unavailable";
  metrics: Record<string, any>;
  evidence: Array<{ claim: string; value?: number | null; unit?: string; source: string; strength: string }>;
  notes: string[];
}

export interface PropEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  status: "observed" | "assumed" | "unavailable";
  strength?: number | null;
  method: string;
  evidence: string[];
}

export interface PropagationGraph {
  nodes: PropNode[];
  edges: PropEdge[];
  legend: Record<string, string>;
  linkage_notice: string;
  assumed_edges: number;
  limitations: string[];
}

export interface EconomicAssessment {
  available: boolean;
  reason: string;
  currency?: string | null;
  lines: Array<{
    label: string;
    quantity?: number | null;
    unit: string;
    rate?: number | null;
    amount?: number | null;
    quantity_source: string;
    rate_source: string;
    note: string;
  }>;
  total?: number | null;
  missing_variables: string[];
  dataset_supplied_values: string[];
  arena_costing_not_exported?: string[];
  disclaimer: string;
}

export interface ScenarioResult {
  scenario: Record<string, any>;
  engine: string;
  engine_note: string;
  horizon_seconds: number;
  replications: number;
  comparisons: Array<{
    station: string;
    station_label: string;
    metric: string;
    unit: string;
    current?: number | null;
    simulated?: number | null;
    delta?: number | null;
    delta_pct?: number | null;
    // Which baseline the `current` column came from: "export (validated)" for
    // utilisation, "re-simulation baseline" for queue length. Never mix the two.
    comparison_basis?: string;
    comparison_basis_note?: string;
    export_reference_value?: number | null;
    comparable?: boolean;
  }>;
  summary: {
    /** Parts released into the line by the export - the same in both runs. */
    baseline_released_parts?: number;
    /** Parts that finished the whole route: the only like-for-like pair. */
    baseline_completed_parts?: number;
    completed_parts?: number;
    throughput_delta_parts?: number;
    wip_parts_end_of_horizon?: number;
    [key: string]: any;
  };
  current_snapshot: Record<string, any>;
  validation: {
    rows: Array<{ station: string; label: string; observed_utilization: number; simulated_utilization: number; residual_pct: number }>;
    mean_abs_residual_pct?: number | null;
    worst_station?: string | null;
    worst_residual_pct?: number | null;
    verdict: string;
    known_ambiguities: string[];
    excluded_stations?: string[];
  };
  economics: EconomicAssessment;
  unavailable: string[];
  advisory: string;
  saved_run_id?: number | null;
}

export interface InspectionPrediction {
  specimen: string;
  label: string;
  label_display: string;
  defect?: string | null;
  product: string;
  confidence: number;
  probabilities: Record<string, number>;
  raw_probabilities: Record<string, number>;
  flip_probabilities: Record<string, number>;
  uncertain: boolean;
  uncertainty_reason: string;
  verdict?: string | null;
  tta_agreement: number;
  ood_distance?: number | null;
  ood_threshold?: number | null;
  out_of_distribution?: boolean;
  ood_note?: string;
  localization_available: boolean;
  localization_note: string;
  attention: Array<{ x: number; y: number; width: number; height: number; weight: number }>;
  attention_note: string;
  model_metrics: Record<string, any>;
  dataset_provenance: Record<string, any>;
  /** Present only on external test-image predictions. */
  external?: {
    id: string;
    source: string;
    original_name?: string | null;
    note?: string;
    statement: string;
    training_data: false;
  };
}

export interface ExternalImageEntry {
  id: string;
  file: string;
  original_name: string;
  source: "upload" | "generated";
  source_detail?: string;
  description?: string;
  note?: string;
  training_data: false;
  created_at: string;
  format: string;
  width?: number | null;
  height?: number | null;
  stored_bytes?: number | null;
}

export interface VisionMetrics {
  available: boolean;
  message: string;
  classes: string[];
  class_counts?: Record<string, number>;
  metrics: Record<string, any>;
  confusion_matrix: number[][];
  training: Record<string, any>;
  localization?: Record<string, any>;
}

export interface AINarrative {
  topic: string;
  subject_id: string;
  source: "llm" | "deterministic";
  provider: string;
  model: string;
  generated_at?: string;
  finding: { finding: string; evidence: string[]; confidence: number; limitations: string[]; recommendation: string };
  grounded_on: Record<string, any>;
  cached: boolean;
  fallback_reason?: string;
  error?: string;
}

// ---------------------------------------------------------------- endpoints
/** The active dataset: what every page reads unless it is told otherwise.
 *
 * Order of preference: the dataset the user explicitly selected (localStorage),
 * then the server's remembered choice, then the newest *uploaded* dataset, and
 * only then a supplied archive. That last step is what keeps the UI from opening
 * on model1/model2/model3 after the user has uploaded their own data. */
export function getActiveDataset(): string {
  return localStorage.getItem("activeDataset") || sessionStorage.getItem("serverActiveDataset") || sessionStorage.getItem("firstPresentDataset") || "";
}

/** Called by the Dataset status/overview loaders once the catalog is known. */
export function rememberFirstPresentDataset(datasets: { key: string; present: boolean; kind: string }[]) {
  const upload = datasets.find((d) => d.present && d.key.startsWith("user:"));
  const first = upload || datasets.find((d) => d.present && d.kind === "simulation");
  if (first) sessionStorage.setItem("firstPresentDataset", first.key);
}

/** Adopt the server's remembered active dataset (used on first load). */
export function rememberServerActive(key: string) {
  if (key) sessionStorage.setItem("serverActiveDataset", key);
}

export function setActiveDataset(key: string) {
  localStorage.setItem("activeDataset", key);
  window.dispatchEvent(new Event("datasetChanged"));
  // Persist the choice server-side so closing and reopening the application
  // lands back in the same case file, not on the newest upload by accident.
  void request<{ active: string }>("/workspace/active", {
    method: "POST",
    body: JSON.stringify({ key }),
  }).catch(() => undefined);
}

/** Keys already handled by the fallback below.
 *
 * Without this, a stale selection ping-pongs: the registry reload triggered by the
 * switch sees the old key again, adopts again, and the page refetches in a loop.
 * Each stale key is therefore resolved exactly once. */
const _staleResolved = new Set<string>();

/** Adopt the dataset the server remembers.
 *
 * Called on first load, and when the remembered key no longer exists (a deleted
 * dataset, or a stale selection from another browser profile) - otherwise every
 * page would keep requesting a dataset the backend cannot find.
 */
export function adoptServerActive(key: string, staleKey?: string) {
  if (!key) return;
  if (staleKey !== undefined) {
    if (_staleResolved.has(staleKey)) return;
    _staleResolved.add(staleKey);
    _staleResolved.add(key);
  } else if (getActiveDataset()) {
    return;
  }
  setActiveDataset(key);
}

/** Uploading a new dataset changes the registry, not just the selection. */
export function notifyWorkspaceChanged() {
  window.dispatchEvent(new Event("workspaceChanged"));
}

export const api = {
  // ------------------------------------------------ workspace / case files
  workspaceDatasets: () =>
    request<{ active: string; count: number; datasets: DatasetRecord[]; note: string }>("/workspace/datasets"),
  workspaceActive: () => request<{ active: string }>("/workspace/active"),
  setWorkspaceActive: (key: string) =>
    request<{ active: string }>("/workspace/active", { method: "POST", body: JSON.stringify({ key }) }),
  workspaceDataset: (keyOrId = getActiveDataset()) =>
    request<{ dataset: DatasetRecord | null; latest_analysis: SavedAnalysis | null; analysis_runs: Array<Record<string, any>>; rate_card: Record<string, any> | null; scenarios: Array<Record<string, any>>; feedback_count: number; loaded: boolean }>(
      `/workspace/datasets/${encodeURIComponent(keyOrId)}`,
    ),
  deleteDataset: (key: string) =>
    request<{ deleted: string; history_retained: boolean; note: string }>(`/workspace/datasets/${encodeURIComponent(key)}`, { method: "DELETE" }),
  runAnalysis: (key = getActiveDataset(), includeAi = true) =>
    request<Record<string, any>>(`/workspace/analysis/run${qs({ key, include_ai: includeAi })}`, { method: "POST" }),
  latestAnalysis: (key = getActiveDataset()) => request<SavedAnalysis>(`/workspace/analysis${qs({ key })}`),
  analysisSummary: (key = getActiveDataset()) => request<AnalysisSummaryCard>(`/workspace/summary${qs({ key })}`),
  rateCard: (key = getActiveDataset()) =>
    request<{ key: string; rate_card: Record<string, any> | null; engineer?: string; updated_at?: string; note: string }>(
      `/workspace/rate-card${qs({ key })}`,
    ),
  saveRateCard: (key: string, rates: Record<string, any>, engineer = "anonymous") =>
    request<{ key: string; rate_card: Record<string, any>; saved: boolean }>(`/workspace/rate-card${qs({ key, engineer })}`, {
      method: "POST",
      body: JSON.stringify(rates),
    }),
  deleteRateCard: (key: string) => request<{ key: string; deleted: boolean }>(`/workspace/rate-card${qs({ key })}`, { method: "DELETE" }),
  repairs: (key = getActiveDataset()) => request<RepairReport>(`/workspace/repairs${qs({ key })}`),
  repairsWithRates: (key: string, rates: Record<string, any>) =>
    request<RepairReport>(`/workspace/repairs${qs({ key })}`, { method: "POST", body: JSON.stringify(rates) }),
  reportSummary: (key = getActiveDataset()) => request<AnalysisSummaryCard>(`/workspace/summary${qs({ key })}`),
  reportUrl: (key: string, format: "md" | "csv" | "json", download = true) =>
    `${BASE}/workspace/report${qs({ key, format, download })}`,
  reportJson: (key = getActiveDataset()) => request<Record<string, any>>(`/workspace/report${qs({ key, format: "json" })}`),

  overview: () => request<Overview>("/datasets/overview"),
  status: () => request<{ status: ProcessingStatus; datasets: DatasetSummary[] }>("/datasets/status"),
  process: (force = false) => request<{ status: ProcessingStatus; message: string }>(`/datasets/process${qs({ force })}`, { method: "POST" }),
  models: () => request<Array<Record<string, any>>>("/datasets/models"),
  columns: (key = getActiveDataset(), documentedOnly = false) =>
    request<ColumnProfile[]>(`/datasets/${key}/columns${qs({ documented_only: documentedOnly })}`),
  quality: (key = getActiveDataset()) => request<Record<string, any>>(`/datasets/${key}/quality`),
  linkage: () => request<Record<string, any>>("/datasets/linkage"),
  design: () => request<Record<string, any>>("/datasets/design"),
  specimens: (perClass = 8) => request<{ classes: Record<string, number>; samples: Record<string, string[]>; total_images: number; note: string }>(`/datasets/specimens${qs({ per_class: perClass })}`),
  uploadDataset: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return request<UploadResult>("/upload/", { method: "POST", body });
  },

  productionSnapshot: (key = getActiveDataset()) => request<ProductionSnapshot>(`/production/snapshot${qs({ key })}`),
  bottlenecks: (key = getActiveDataset()) => request<Record<string, any>>(`/production/bottlenecks${qs({ key })}`),
  anomalies: (key = getActiveDataset(), top = 25) => request<AnomalyReport>(`/production/anomalies${qs({ key, top })}`),
  regimes: (key = getActiveDataset()) => request<Record<string, any>>(`/production/regimes${qs({ key })}`),
  demandResponse: (key = getActiveDataset()) => request<Record<string, any>>(`/production/demand-response${qs({ key })}`),
  calibration: (key = getActiveDataset()) => request<CalibrationReport>(`/production/calibration${qs({ key })}`),
  association: (key = getActiveDataset()) => request<Record<string, any>>(`/production/association${qs({ key })}`),

  cases: (key = getActiveDataset()) =>
    request<{ model_key: string; cases: Array<{ case_id: string; label: string; kind: string; scope: string; description: string }> }>(
      `/diagnostics/cases${qs({ key })}`,
    ),
  case: (caseId: string, key = getActiveDataset()) => request<ForensicCase>(`/diagnostics/case${qs({ case_id: caseId, key })}`),
  propagation: (key = getActiveDataset()) => request<PropagationGraph>(`/diagnostics/propagation${qs({ key })}`),
  stations: (key = getActiveDataset()) => request<{ model_key: string; stations: Array<StationRow & { severity: string; baseline: Record<string, any> }> }>(`/diagnostics/stations${qs({ key })}`),

  // Agent (answered by the AI narrative endpoint; scope picks the evidence bundle)
  chat: (message: string, scope: string, key = getActiveDataset()) =>
    request<AINarrative>("/ai/ask", {
      method: "POST",
      body: JSON.stringify({ question: message, scope, subject_id: key || null }),
    }),
  economics: (key = getActiveDataset()) => request<EconomicAssessment>(`/economics/assessment${qs({ key })}`),
  economicsWithRates: (key: string, rates: Record<string, any>) =>
    request<EconomicAssessment>(`/economics/assessment${qs({ key })}`, { method: "POST", body: JSON.stringify(rates) }),
  economicsRequirements: () => request<Record<string, any>>("/economics/requirements"),

  simulationOptions: (key = getActiveDataset()) => request<Record<string, any>>(`/simulation/options${qs({ key })}`),
  runScenario: (spec: Record<string, any>, rates?: Record<string, any> | null, engineer = "anonymous", key = getActiveDataset()) =>
    request<ScenarioResult>(`/simulation/run${qs({ engineer, key })}`, {
      method: "POST",
      body: JSON.stringify({ spec, rates: rates ?? null }),
    }),
  simulationHistory: (key = getActiveDataset(), limit = 20) =>
    request<Array<Record<string, any>>>(`/simulation/history${qs({ key, limit })}`),
  simulationValidation: (key = getActiveDataset()) => request<Record<string, any>>(`/simulation/validation${qs({ key })}`),

  visionMetrics: () => request<VisionMetrics>("/inspection/metrics"),

  // external test images (test-only, never training data)
  externalInfo: () =>
    request<{
      training_data: false;
      statement: string;
      storage_directory: string;
      supported_formats: string[];
      max_upload_bytes: number;
      generator_kinds: string[];
      count: number;
      counts_by_source: Record<string, number>;
      model_available: boolean;
    }>("/external-images/info"),
  externalList: (source?: "upload" | "generated", limit = 60) =>
    request<ExternalImageEntry[]>(`/external-images/list${qs({ source, limit })}`),
  externalUpload: (file: File, note = "") => {
    const body = new FormData();
    body.append("file", file);
    return request<ExternalImageEntry>(`/external-images/upload${qs({ note })}`, { method: "POST", body });
  },
  externalGenerate: (kind: "mixed" | "variation" | "nonfactory", count = 6, seed?: number) =>
    request<{ generated: ExternalImageEntry[]; count: number; training_data: false; statement: string; note: string }>(
      `/external-images/generate${qs({ kind, count, seed })}`,
      { method: "POST" },
    ),
  externalDelete: (id: string) => request<{ deleted: string }>(`/external-images/${id}`, { method: "DELETE" }),
  externalPredict: (id: string) =>
    request<InspectionPrediction>(`/external-images/predict/${id}`, { method: "POST" }),
  externalImageUrl: (id: string) => `${BASE}/external-images/image/${id}`,
  inspectSamples: (perClass = 8) => request<{ classes: Record<string, number>; samples: Record<string, string[]>; model_available: boolean; note: string }>(`/inspection/samples${qs({ per_class: perClass })}`),
  predict: (specimen: string) => request<InspectionPrediction>(`/inspection/predict${qs({ specimen })}`, { method: "POST" }),
  thumbnailUrl: (name: string, size = 160) => `${BASE}/inspection/thumbnail${qs({ name, size })}`,
  imageUrl: (name: string) => `${BASE}/inspection/image${qs({ name })}`,

  feedback: (payload: Record<string, any>) =>
    request<Record<string, any>>("/review/feedback", {
      method: "POST",
      body: JSON.stringify({ dataset_key: getActiveDataset(), ...payload }),
    }),
  feedbackList: (key = getActiveDataset(), limit = 100) =>
    request<Array<Record<string, any>>>(`/review/feedback${qs({ key, limit })}`),
  reviewSummary: (key = getActiveDataset()) => request<Record<string, any>>(`/review/summary${qs({ key })}`),
  assumptions: () => request<{ assumptions: Array<Record<string, any>>; linkage: Record<string, any>; note: string }>("/review/assumptions"),
  createAssumption: (payload: Record<string, any>) => request<Record<string, any>>("/review/assumptions", { method: "POST", body: JSON.stringify(payload) }),
  deleteAssumption: (id: number) => request<Record<string, any>>(`/review/assumptions/${id}`, { method: "DELETE" }),

  aiStatus: () => request<Record<string, any>>("/ai/status"),
  aiNarrative: (topic: string, subjectId?: string | null, key = getActiveDataset(), refresh = false) =>
    request<AINarrative>(`/ai/narrative${qs({ topic, subject_id: subjectId, key, refresh })}`),
  aiAsk: (question: string, scope = "overview", subjectId?: string | null, key = getActiveDataset()) =>
    request<AINarrative>("/ai/ask", { method: "POST", body: JSON.stringify({ question, scope, subject_id: subjectId, key }) }),
  aiLogs: (limit = 25) => request<Record<string, any>>(`/ai/logs${qs({ limit })}`),
};
