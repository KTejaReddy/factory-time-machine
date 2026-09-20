# API_REFERENCE.md

Every HTTP endpoint of Factory Time Machine, with inputs, processing, side effects and error
behaviour. Base URL in development: `http://127.0.0.1:8001` (the Vite dev server proxies `/api`
there). Interactive docs: `/docs`.

Legend: **AI?** — does the handler involve the language model at all (all numbers still come
from Python). **DB?** — does it read/write the SQLite store.

---

## Meta

### `GET /api/health`
Liveness + configuration snapshot: catalog state, whether the LLM is enabled, `advisory_only: true`.
**Errors:** none.

---

## Datasets & provenance (`routers/datasets.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/datasets/overview` | Everything the UI needs for provenance, gating and data quality | — | Reads catalog status, summaries, issues, linkage, capabilities, provenance | Combined object | — |
| GET | `/api/datasets/status` | Lightweight progress poll | — | Status tracker + dataset summaries | `{status, datasets}` | — |
| POST | `/api/datasets/process` | (Re)build the catalog | `force` (bool, query) | Starts the background build thread (idempotent while running) | `{status, message}` | — |
| GET | `/api/datasets/models` | Documented plant metadata the analytics are built on | — | Serialises `domain.MODELS` (stages, factors, stations, capacities, sources) | List of models | — |
| GET | `/api/datasets/{key}/columns` | Column schema/profile | `key`, `group`, `documented_only` | Filters cached profiles | `ColumnProfile[]` | **404** unknown key |
| GET | `/api/datasets/{key}/quality` | Data-quality report | `key` | Missing cells, duplicates, constant columns, documented vs undocumented counts | Object | **404** unknown key |
| GET | `/api/datasets/linkage` | Why the datasets cannot be joined | — | Linkage report + arguments + the Arena costing constructs that are never exported | Object | — |
| GET | `/api/datasets/design` | MATLAB surrogate arrays | — | Variable shapes, verified cross-references, undocumented predictors | Object | — |
| GET | `/api/datasets/specimens` | Deterministic specimen list | `per_class` (1–50) | Archive index scan | `{classes, samples, total_images, note}` | — |

### Uploading your own dataset

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| POST | `/api/upload/` | Add a CSV of your own data | multipart `files` (one or more `*.csv`) | Basename sanitisation (a crafted `../../app/main.py.csv` lands as `main.py.csv` inside `data/user_datasets`) → 25 MB streaming cap → must parse as a table → catalog rebuild → profiler → capability map | `{message, files, uploaded:[{key, label, rows, columns, capabilities}], status}` | **400** not a CSV / empty / unreadable · **413** above the 25 MB cap |

The uploaded file is listed by `/api/datasets/overview` under the key `user:<file stem>` and appears on the Datasets page with its capability map. That map has the same shape as the built-in datasets — `{"available": {vision, production, anomaly, forensics, economics, simulation}, "reasons": {...}, "detected": {...}}` — with a reason for every feature that is switched off, so the UI never shows a broken tab without saying why. The upload also creates a **case file** for the dataset (see the workspace section below) and becomes the active dataset. `available.simulation` is `false` for an upload because the what-if engine is calibrated to the supplied Model 3 export — that reason is returned, not hidden.

---

## Dataset workspace / case files (`routers/workspace.py`)

Every endpoint is dataset-scoped by an explicit `key` (or the stable dataset id `ds-…` where a
path is used). Nothing is inferred from "the current page". See
`MULTI_DATASET_ARCHITECTURE.md` for the model and `FINAL_REPORT_FORMAT.md` for the report.

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/workspace/datasets` | The dataset history: every case file, newest first | — | Reconciles presence flags against the source files, reads registry rows, counts each dataset's saved artifacts | `{active, count, datasets:[{id, key, name, kind, source_file, uploaded_at, status, status_label, rows, columns, profile, capabilities, present, artifacts}]}` | — |
| GET | `/api/workspace/datasets/{key-or-id}` | One case file's header + saved history | path key or id | Record + last 5 analysis runs + rate card + scenario history + feedback count | `{dataset, latest_analysis, analysis_runs, rate_card, scenarios, feedback_count, loaded}` | **404** unknown |
| DELETE | `/api/workspace/datasets/{key-or-id}` | Remove an uploaded CSV | path key or id | Deletes the file, keeps the record (flagged `file_missing`) and its history | `{deleted, record, note}` | **400** for a supplied archive · **404** unknown |
| GET | `/api/workspace/active` | Read the active dataset | — | Server preference, else newest upload, else any present dataset | `{active}` | — |
| POST | `/api/workspace/active` | Set the active dataset | `{key}` | Persists the preference | `{active}` | **400** missing key · **409** source file missing · **404** unknown |
| POST | `/api/workspace/analysis/run` | Compute **and store** the full result set | `key`, optional `rates`, `use_saved_rates`, `include_ai` | Quality, process, production, anomaly, forensics, propagation, economics, repairs + AI narrative; persisted as an `analysis_runs` row | `{dataset, generated_at, capabilities, sections, summary, ai, saved_at, run_id}` | **404** unknown · **409** source file missing |
| GET | `/api/workspace/analysis` | The saved result set | `key` | DB read | `{status:"saved", …}` or `{status:"not_run", reason}` | **404/409** |
| GET | `/api/workspace/summary` | The "analysis complete" card | `key` | Reads the saved run | `{status, dataset, generated_at, analysis_run_id, main_issue, repair, estimated_impact, currency, confidence, highlights:{quality, process, production, economics}, formats:[{format,label,url}], limitations}` | **404/409** |
| GET | `/api/workspace/rate-card` | This dataset's rates | `key` | DB read | `{key, rate_card, updated_at}` | **404/409** |
| POST | `/api/workspace/rate-card` | Store rates for this dataset | `key`, `CostRateCard`, `engineer` | Upsert (one row per dataset) | `{key, rate_card, saved:true, updated_at}` | **404/409 · 422** |
| DELETE | `/api/workspace/rate-card` | Remove the rate card | `key` | Delete | `{key, deleted}` | **404/409** |
| GET | `/api/workspace/repairs` | Repair options + the selected repair | `key` | Discovers candidates from the dataset, resolves rates, ranks | `{status, statement, currency, currency_source, rate_card_used, rates, dataset_cost_columns, cheapest_supported_effective_repair, candidates, reductions_assumed, limitations}` | **404/409** |
| POST | `/api/workspace/repairs` | Same, with rates supplied in the body | `key`, optional `rates` | As above, body overrides the stored card for this call | Same | **404/409 · 422** |
| GET | `/api/workspace/report` | The final report | `key`, `format=json\|md\|csv`, `refresh`, `download` | Builds from the saved run (or recomputes when `refresh=true`) | JSON payload, or Markdown/CSV body, optionally as an attachment named `report-<dataset id>-<timestamp>.<ext>` | **404/409 · 422** bad format · **500** if the dataset has no saved run and cannot be analysed |

**Isolation guarantees encoded here:** a key that is not loaded returns 404; a known case file whose
source file is gone returns 409 with a plain-language explanation instead of a 500; every stored
artifact is keyed by `dataset_key`, so no endpoint can return another dataset's analysis, rate
card, scenarios, feedback or report.

---

## Production & diagnostics (`routers/analytics.py`)

All endpoints take `key` (default `model3`, one of `model1|model2|model3`) and return **404**
for an unknown key.

| Method | Route | Purpose | Output highlights | Errors |
|---|---|---|---|---|
| GET | `/api/production/snapshot` | Production page payload | throughput + its definition, WIP, per-station rows, bottleneck candidates, ranking method/weights, headroom, calibration, utilisation profile, explicit `unavailable` list | **409** dataset not loaded |
| GET | `/api/production/bottlenecks` | The ranking on its own | ranking, weights, unmeasured stations, headroom | — |
| GET | `/api/production/anomalies` | Unusual simulated runs | `top` runs with drivers, threshold, method sentence, limitations | — |
| GET | `/api/production/regimes` | Low- vs high-output regimes | per-metric means, difference, CI, Cohen's d, plus the "no timestamps, no drift analysis" note | — |
| GET | `/api/production/demand-response` | Models 1–2 designed factor | points per Demand level, saturation, cross-validated surrogate R² | — |
| GET | `/api/production/calibration` | Utilisation-identity check | per-station observed vs implied, residual, implied capacity, verdict, limitations | — |
| GET | `/api/production/association` | Utilisation vs outcome | per-station Pearson/Spearman/CI/r², direction statement | — |
| GET | `/api/diagnostics/cases` | Investigable cases | congestion, lowest/highest output, regime contrast, top anomalies | — |
| GET | `/api/diagnostics/case` | One forensic case | ordered timeline, first divergence, co-occurring, evidence, confidence + basis, limitations, data-quality notes, causal caveat | **400** bad run index · **409** dataset not loaded |
| GET | `/api/diagnostics/propagation` | Failure-propagation graph | nodes/edges with observed-vs-assumed status, legend, linkage notice, limitations | **409** dataset not loaded |
| GET | `/api/diagnostics/stations` | Per-station detail for the graph panel | station rows + baseline stats + severity | — |

---

## Economics (`routers/analytics.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/economics/assessment` | Can money be computed? | `key` | With no rates: `available: false`, reason, missing variables, disclaimer | Assessment | **404** unknown key |
| POST | `/api/economics/assessment` | Advisory figure from **your** rates | `key` + body `CostRateCard` (`currency`, `margin_per_unit`, `cost_per_unit_scrapped`, `holding_cost_per_unit_hour`, `downtime_cost_per_hour`, `rework_cost_per_unit`) | Quantities from the dataset × user rates; every line records `quantity_source` and `rate_source`; scrap/rework/downtime lines state that no quantity exists | Assessment with line items and total | **404** unknown key |
| GET | `/api/economics/requirements` | What a rate card must contain | — | Constants from `economics.py` | Object | — |

**AI?** No. **DB?** No.

---

## Inspection (`routers/inspection.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/inspection/metrics` | Model provenance and measured quality | — | Reads the metrics artefact | availability, message, classes, per-class metrics, confusion matrix, training record, `localization.available: false` with the reason | — |
| GET | `/api/inspection/samples` | Specimens for the grid | `per_class` | Archive index | classes, samples, `model_available`, the no-identifier note | — |
| GET | `/api/inspection/image` | Full image from the archive | `name` | Locked member read | PNG bytes, `X-Dataset-Class` header | **404** not in archive |
| GET | `/api/inspection/thumbnail` | Thumbnail | `name`, `size` (32–512) | Cached, atomic write | PNG bytes | **404** |
| POST | `/api/inspection/predict` | Full inspection of an archive image | `specimen` (`class/index` or path) | Preprocess → CNN → TTA → OOD distance → uncertainty → Grad-CAM | Prediction, confidence, probabilities, TTA agreement, OOD distance/threshold/flag, uncertain + reason, attention regions, model metrics, provenance | **404** specimen · **409** no trained model |

**AI?** No (this is the CNN, not the LLM).

---

## External test images (`routers/external_images.py`)

Test-only images. **Nothing here can enter training data** — the training cache builder reads
`train.zip` exclusively, and every record carries `training_data: false`.

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/external-images/info` | The separation rule + store facts | — | Index counts and constants | `training_data: false`, the statement, storage directory (names no absolute path, so it reads the same on every OS), formats, size cap, generator kinds, model availability | — |
| GET | `/api/external-images/list` | Stored test images | `source` (`upload|generated`), `limit` | Index | `ExternalImageEntry[]` | **422** bad `source` value |
| POST | `/api/external-images/upload` | Upload your own image | multipart `file`, `note` (query) | Magic-byte check → PIL decode → terminator/size checks → optional downscale to 512 px → store under a server-generated UUID name | The stored entry | **400** not an image / truncated / empty / > 10 MB |
| POST | `/api/external-images/generate` | Generate synthetic robustness probes | `kind` (`mixed|variation|nonfactory`), `count` (1–24), `seed` | Archive image + photometric/geometric variation, or drawn non-factory shapes; deterministic for a seed | generated entries + the test-only note | **422** bad `kind`/`count` |
| GET | `/api/external-images/image/{id}` | Serve a stored test image | path `id` | Index lookup, file read | Image bytes, `X-Training-Data: false` | **404** unknown id or missing file |
| DELETE | `/api/external-images/{id}` | Remove a test image | path `id` | Unlink + index update | `{deleted, training_data: false}` | **404** unknown id |
| POST | `/api/external-images/predict/{id}` | Full inspection of a test image | path `id` | **The same pipeline as archive inspection** + the external provenance block | Prediction with `external` block (source, note, `training_data: false`, "External Test Image - Not Used for Training") | **404** unknown id · **409** no trained model |

---

## What-if simulation (`routers/simulation.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/simulation/options` | What can honestly be simulated | — | Adjustable stations (with reasons for the others), engines, headroom, not-supported list | Object | — |
| POST | `/api/simulation/run` | Run a scenario | body `{spec, rates}`, query `engineer`, `save` | `_validate_spec` → discrete-event re-simulation or demand interpolation → comparisons (utilisation vs export, queue vs engine baseline) → validation residuals → gated economics → optionally stored | Scenario result + `saved_run_id` | **400** invalid scenario (unknown/non-adjustable station, zero delta, bad replication count…) · **409** dataset missing |
| GET | `/api/simulation/history` | Stored runs | `limit` | DB read | Rows | — |
| GET | `/api/simulation/validation` | Baseline validation of the engine | — | Unmodified run vs exported columns | residuals, worst station, verdict, known ambiguities, advisory notice | **409** dataset missing |

**DB?** Only when `save=true`.

---

## AI narrative (`routers/ops.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/ai/status` | Why the LLM is or is not in use | — | Settings + sliding-window usage | `llm_enabled`, provider, model, base URL, reason, calls last hour, limit, contract fields, usage summary | — |
| GET | `/api/ai/narrative` | Narrative for a topic | `topic` (`overview|case|station|scenario`), `subject_id`, `key`, `refresh` | Backend assembles the evidence bundle → LLM (if allowed) → `AIFinding` validation → deterministic fallback | Narrative with `source` (llm/deterministic), provider/model, the finding contract, and `grounded_on` (the compacted evidence) | **400** missing `subject_id` for the station topic · **404** unknown station · **422** bad topic |
| POST | `/api/ai/ask` | Answer a question from evidence only | body `AIQuestion` (`question` 3–600 chars, `scope`, `subject_id`) | Same pipeline; the question is appended to the evidence prompt | Narrative | **400** missing `subject_id` · **404** unknown station |
| GET | `/api/ai/logs` | Every AI attempt | `limit` | DB read | logs + usage | — |

The LLM never receives raw datasets — only the compacted bundle returned in `grounded_on`.
If a provider call fails, the response is the deterministic narrative with `fallback_reason`
naming the provider error, so a broken integration is visible instead of silent.

---

## Human review (`routers/ops.py`)

| Method | Route | Purpose | Input | Processing | Output | Errors |
|---|---|---|---|---|---|---|
| POST | `/api/review/feedback` | Record an engineer verdict | `FeedbackIn`: `finding_id`, `finding_kind`, `finding_title`, `decision` (confirmed/rejected/needs_review), `note`, `engineer`, `payload` | Inserts a row with the exact payload shown | `{item, model_retrained: false, note}` | **422** invalid decision/shape |
| GET | `/api/review/feedback` | History | `limit` (1–500) | DB read | Rows | — |
| GET | `/api/review/summary` | Counts + retraining honesty | — | Aggregates | total, counts, kinds, `retraining.implemented: false` with the reason | — |
| GET | `/api/review/assumptions` | Declared defect→station edges | — | DB read + linkage report | assumptions + note | — |
| POST | `/api/review/assumptions` | Declare an edge | `LinkageAssumptionIn` | Inserts with author + timestamp | The row + "recorded as assumed" | **422** |
| DELETE | `/api/review/assumptions/{id}` | Remove a declared edge | path `id` | Delete | `{deleted}` | **404** unknown id |

**`model_retrained` is always `false`.** Nothing in this build consumes feedback for training.

---

## Frontend delivery

| Method | Route | Behaviour |
|---|---|---|
| GET | `/` | Serves the built SPA when `frontend/dist` exists; otherwise a JSON hint pointing at `/docs` and the dev-server command. |
| GET | `/{path:path}` | SPA fallback (serves existing files directly, else `index.html`). |

---

## Error-handling conventions

* Domain validation errors → **400** with a plain-language `detail` (e.g. *"cannot change
  capacity at 'blanking': documented stage with no exported utilisation column…"*).
* Unknown dataset/specimen/station/id → **404**.
* Dataset not loaded / no trained model → **409** (the request is valid; the prerequisite is absent).
* Malformed schema → **422** (pydantic).
* Unexpected exceptions → **500** with the exception type only; the full traceback goes to the
  log, never to the client.

The frontend's `request()` wrapper converts any non-2xx response into an `ApiError` carrying
that `detail`, and every page renders it through `ErrorBox` with a retry button — a raw stack
trace is never shown to a user.
