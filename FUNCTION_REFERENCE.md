# FUNCTION_REFERENCE.md

A function-by-function walkthrough of the code that actually exists in this repository.

> **Why this file was rewritten:** the previous version described functions that are not in
> the code (`_load_dataset()` using Dask, `compute_gradcam`, `inspect_image`,
> `return_features=True`). Dask is not a dependency of this project, and none of those
> names appear in the source. Every entry below was read out of the files it names.

Format for each entry: **purpose · input · output · logic · called by · error handling · status**.

---

## 1. Configuration and storage

### `config.py`

| Function | Purpose / notes |
|---|---|
| `_load_dotenv()` | Minimal `.env` reader (no `python-dotenv` dependency). Reads `<project root>/.env` once at import, `os.environ.setdefault` so real env vars win. **In:** nothing. **Out:** side effect on `os.environ`. **Errors:** silently skips a missing file. ✅ |
| `Settings` (dataclass, frozen) | Every tunable resolved from the environment: AI provider/key/URL/model/timeout/tokens/temperature/rate limit/disable, `DATABASE_URL`, `DIVERGENCE_Z` (2.5), `DIVERGENCE_EFFECT_D` (0.15), `STATS_SAMPLE_ROWS` (60 000), `VISION_UNCERTAINTY_THRESHOLD` (0.60), `SIM_HORIZON_SECONDS` (86 400), `SIM_RANDOM_SEED`, `CORS_ORIGINS`, `LOG_LEVEL`. **No secret is hard-coded anywhere.** Properties: `ai_enabled`, `vision_model_path`, `vision_metrics_path`. |
| `ensure_dirs()` | Creates `data/{raw,cache,artifacts}`. Idempotent. |

### `db.py` (SQLAlchemy 2.0, SQLite by default, PostgreSQL-compatible schema)

| Object / function | Purpose |
|---|---|
| `FeedbackItem` | One engineer verdict: `finding_id`, `finding_kind`, `finding_title`, the **exact payload shown** (`payload` JSON text), `decision` (confirmed/rejected/needs_review), `note`, `engineer`, UTC `created_at`. |
| `LinkageAssumption` | An engineer-declared defect-class → station edge, with `rationale`, `author`, `active`, `created_at`. Exists because the datasets share no join key. |
| `ScenarioRun` | A stored what-if run: scenario spec, baseline snapshot, result comparisons, validation, engineer, timestamp. |
| `Narrative` | A cached AI narrative plus the evidence bundle it was grounded on and an evidence hash. |
| `AICallLog` | Every AI API attempt: topic, provider, model, ok, latency, prompt size, error text. |
| `init_db()` | `Base.metadata.create_all`. Called once at startup. |
| `session()` | Context manager: session, commit, rollback on exception, always close. |
| `list_rows(model, limit, order_desc)` | Newest-first select with a limit; used by every list endpoint. |
| `insert_row(row)` | Add, flush, refresh, return — so the caller sees the generated id and timestamp. |

### `logging_setup.py`

`setup_logging()` — console + rotating file handler (`data/cache/backend.log`, 2 MB × 3), silences uvicorn access noise. `StageTimer` — lap timing for ingestion stages.

---

## 2. Dataset catalog — `services/catalog.py`

| Function | Purpose · input · output · logic · status |
|---|---|
| `ProcessingStatusTracker` (`update/snapshot/start/fail/done`) | Thread-safe progress for the background build (state, progress, step, message, stage timings). Polled by `GET /api/datasets/status`. ✅ |
| `Catalog.start_background_build(force)` | Starts one daemon thread; refuses to start a second while one is alive. Called at startup (lifespan) and by `POST /api/datasets/process`. |
| `Catalog.build(force)` | **The pipeline.** Index the image archive → load model1/2/3 (parquet cache) → build column profiles + data-quality issues → derive Model-3 part counters → mark ready. Non-blocking lock; exceptions are logged and reported as `failed` with the message. Warm cache ≈ 6 s, cold ≈ 12 s. ✅ |
| `_index_vision()` | Reads the ZIP central directory only (no extraction). Keeps entries of the form `train/<class>/<file>.png`; sorts by name; records class counts. Adds an `info` issue stating that **no annotation files exist** (so localization is unavailable). |
| `vision_zip()` | Lazily opens the shared `ZipFile` under `_zip_lock`. The lock is load-bearing: one ZipFile shares one file handle and a read is seek-then-read, so concurrent reads corrupt bytes (regression-tested). |
| `_load_model(key, force)` | Reads `<key>.parquet` when a `(size, mtime)` fingerprint matches, else parses the CSV (`utf-8-sig`, float32, drops unnamed columns) and writes the parquet cache. **Input:** dataset key. **Output:** frame in memory. **Errors:** missing file → recorded issue, no crash. |
| `_load_sample(key, df)` | Deterministic stride sample (`STATS_SAMPLE_ROWS`) used for plotting/model fitting. |
| `_build_profiles()` | Per-column profile (count, missing, mean, std, min, p05, median, p95, max, documented flag, note) plus measured data-quality issues: missing cells, duplicate rows, constant columns (which is how "no timestamp" is proven). |
| `_derive_model3_parts()` | Sums the 16 `c_CellX__SKUY` counters per row → total parts per run, plus per-SKU sums. The basis of throughput everywhere downstream. |
| `model_df(key)` / `sample_df(key)` / `profile_map(key)` | Accessors; `model_df` raises `KeyError` when the dataset is not loaded. |
| `population_stats(key)` | Full-population mean/std/min/max per column, computed once and cached. |
| `population_quantiles(key, cols)` | Quantiles on the deterministic sample (the API says which). |
| `mat_summary_lazy()` | Reads `3000Samplesv3.mat` once: variable shapes/dtypes plus the **verified** cross-references to `Model_3.csv` columns and the undocumented `Predictors[1..3]` statement. |
| `image_bytes(name)` | Reads one archive member **under the lock**; returns raw bytes. |
| `thumbnail(name, size)` | Down-scaled PNG, memoised in `data/cache/thumbnails`, written via temp file + atomic `os.replace` so a concurrent reader never sees a half-written file. |
| `find_specimen(specimen)` | Resolves `class/index` (so `crack/0` → `train/crack/crack_00000.png`) or an exact archive path. **Errors:** `None` when absent; routers turn that into 404. |
| `dataset_summaries()` | The per-dataset cards (rows, columns, source file, description, warnings, extras). |
| `linkage_report()` | The measured join analysis: `joinable: false`, empty `shared_keys`, per-domain key table, and a plain-language statement. |
| `capabilities()` | Feature gate: what the data supports (`inspection_classification`, `whatif_des`, …) and, for everything unsupported, a **stated reason** (`localization`, `economic_impact`, `downtime`, `scrap`, `rework`, `defect_rate_per_batch`). |
| `provenance()` | Source files, horizon + its derivation, the documents the plant metadata came from, derived columns. |

---

## 3. Documented plant metadata — `services/domain.py`

Pure data transcribed from `Model 1/2/3.pdf`, `ParametersFile.xls` and `Readme.txt`.

| Object | Content |
|---|---|
| `StationSpec` | key, label, stage, route index, units, capacity + `capacity_source`, utilisation column, queue columns, WIP/counter columns, processing time + source + note. |
| `MODEL3_STATIONS` | Blanking, Press 1–4, Assembly Cell 1–4, Paint 1–2, Quality, Forklift, Warehouse 1–4 buffers — 18 stations with their documented capacities and times. |
| `MODEL3_SKU_ROUTING` / `MODEL3_ASSEMBLY_TIME` / `MODEL3_SKU_SHARE` | SKU → allowed cells; per-SKU assembly seconds (25/15/23/17); discrete-uniform 0.25 SKU mix. |
| `MODEL3_ALL_CELL_COUNTERS`, `cell_counter_columns(i)` | The 16 counter columns, including the export's inconsistent single-underscore `c_Cell1_SKU1`. |
| `MODELS` | The registry the whole backend iterates over (models 1, 2, 3). |
| `ROUTE_ORDER`, `station_sort_key` | The canonical route used for *ordering* divergence, since there are no timestamps. |
| `MODEL3_NOMINAL_HORIZON_SECONDS` (86 400) + `..._SOURCE` | The horizon and how it was derived (Time_Now = 24 × documented times/counters). |

---

## 4. Analytics — `services/analytics.py`

| Function | Purpose · logic · output |
|---|---|
| `finite(values)` / `describe(values)` | NaN-stripping and a five-number summary with `None`s (never fake zeros). |
| `fisher_ci(r, n, conf)` | Fisher z-transform confidence interval. Returns `(None, None)` when n < 4 or |r| ≥ 1. |
| `effect_label(r)` | negligible / weak / moderate / strong / very strong from |r| thresholds 0.10/0.25/0.45/0.65. |
| `association(x, y, max_n)` | **Pearson + Spearman + Fisher CI + r² + effect label.** Subsamples to ≤120 000 pairs for speed. Returns `note` stating that significance is meaningless at this n and effect size is what matters. |
| `robust_z(values)` | `(x − median)/(1.4826·MAD)` with std fallback; zeros when the scale is 0. |
| `calibration_report(catalog, key)` | **The trust anchor.** Recomputes `utilisation = work/(capacity×horizon)` for every station with exported counters, compares with the exported `*_Util`, inverts the identity to an *implied capacity* and flags inconsistency beyond ±10 %. Returns rows, residuals, verdict, implied capacities and explicit limitations (including that Cell 4 contradicts the documentation). |
| `AnomalyModel.fit(catalog)` | Standardise selected features → eigendecomposition → keep ≤6 components → threshold = 99.5th percentile of the fit sample. Records `method` as a sentence. Raises `ValueError` when fewer than 200 usable rows. |
| `AnomalyModel._select_features` | Model 3: utilisation + queue columns + counter columns (log1p). Models 1–2: station utilisation + queue columns. |
| `AnomalyModel.score(...)` / `_score_matrix(...)` | Mahalanobis distance in scaled PCA space. |
| `AnomalyModel.drivers(df, row, top)` | The features with the largest standardised deviation for one run, with raw value, direction and baseline mean — this is what makes an anomaly explainable. |
| `AnomalyModel.feature_table()` | Per-feature loading on the leading component (cheap importance proxy). |
| `anomaly_model(...)` / `anomaly_report(...)` | Cached model; top-N runs with drivers, plus limitations (anonymous replications, sample-fitted threshold, "unusual ≠ defective"). |
| `regime_contrast(catalog, key)` | Low-output (≤p20) vs high-output (≥p80) regimes: mean difference, 95 % CI, **Cohen's d**, sorted by |d|; states that time-based drift is impossible here and why. |
| `demand_response(catalog, key)` | Per-Demand-level mean throughput/utilisation/waiting (Models 1–2), saturation detection (first level ≥95 %), and the cross-validated surrogate block. |
| `_demand_surrogate(df, target)` | GradientBoostingRegressor + `cross_val_predict` (5-fold) → out-of-fold R², RMSE, MAE. Degrades to `available: false` with a reason if sklearn or rows are missing. |
| `stage_association(catalog, key)` | Per-station utilisation/queue vs outcome (parts per run): Pearson, CI, r², effect label. Sorted by r². **Direction is documented, never inferred from the correlation.** |

---

## 5. Bottleneck — `services/bottleneck.py`

| Function | Purpose · logic |
|---|---|
| `station_rows(catalog, key)` | One dict per documented station: capacity + source, utilisation (+ min/max and whether it was exported), queue means/maxima per column, WIP, processed units, `headroom = 1/util − 1`, and `unavailable` entries naming exactly what is missing. For cells whose own queue is 0, `wip_evidence` falls back to the warehouse buffers **and says so**. |
| `_queue_rank(rows)` | Converts WIP evidence to a rank in [0,1] across stations (units differ, so rank is the comparable form). |
| `_capacity_constraint(rows)` | `1/(1+capacity)`, min-max normalised; fewer servers = tighter constraint. |
| `rank_bottlenecks(catalog, key)` | **`0.80 × utilisation_norm + 0.12 × queue_rank + 0.08 × capacity`** (renormalised over available terms). Returns ranking, weights, unmeasured stations, the method sentence, and per-station evidence text. Unmeasured stations get `score = None`, never 0. |
| `capacity_headroom(catalog, key)` | Sorts stations by utilisation and states `load_headroom = 1/util − 1`, i.e. how much extra load before saturation. Labelled arithmetic, not simulation. |
| `production_snapshot(catalog, key)` | The Production page payload: throughput (with its definition), WIP, per-station rows, bottleneck candidates, ranking method/weights, headroom, calibration, utilisation profile, and the explicit `unavailable` list (cycle time, downtime, changeovers, scrap/rework). |

---

## 6. Forensics — `services/forensics.py`

| Function | Purpose · logic |
|---|---|
| `station_baseline(catalog, key)` | Per-station **median + MAD** baselines (queues in log1p space), cached per process. Median/MAD because queue columns are heavy-tailed. |
| `severity_from_z(z, statistic)` | Different bands for different statistics: |d| ≥ 0.8/0.5, |z| ≥ 4/2.5 — so a single replication and a group contrast are never judged on one scale. |
| `list_cases(catalog, key)` | The investigable cases: the congestion case (busy yet low output), lowest/highest-output 1 %, the regime contrast, and the top 5 anomaly runs. |
| `_congestion_index(catalog, key)` | `rank(utilisation) − rank(output)` per run: positive = worked hard, produced little. |
| `_group_selection(...)` | Resolves a case id to the index of runs it covers plus the selection description. |
| `_deviation_events(...)` | Per-station, per-metric divergence along the route: utilisation and log1p queue, choosing the larger magnitude as the primary metric, with reader-facing evidence sentences (value vs population, delta %, statistic) plus capacity provenance and the "cell queue is 0" note. |
| `_event_from_metric(...)` | The measurement itself: group cases use Cohen's d vs the population mean with the t-like statistic `d·√n` reported separately; single runs use robust z vs median/MAD. Returns value, baseline, delta %, z, significance, column, row count. |
| `build_case(catalog, key, case_id)` | **The core forensic output.** Selects the runs → computes divergence per station → orders by the documented route → first material divergence (`min` |z| over the threshold) → coherence share downstream → transparent confidence (0.4 count + 0.3 coherence + 0.3 calibration quality, capped 0.95, with every point listed in `confidence_basis`) → evidence chain, limitations, data-quality notes and the causal caveat. Raises `ValueError` for an out-of-range run index. |
| `_confidence(...)` | The heuristic above; explicitly *not* a probability that a root cause is true, and it says so. |
| `_case_evidence(...)` | Human-readable evidence rows: first divergence, co-occurring divergences, output delta, and the statistic/selection used, each with a strength label. |
| `_partial_correlation(x, y, controls)` | Residualise x and y on the controls by least squares, correlate the residuals. Returns `None` when fewer than 50 usable rows or a degenerate case. |
| `propagation_graph(catalog, key, assumptions)` | Builds the interactive graph: stage nodes with measured metrics and evidence; route edges (direction documented, strength = Pearson r); **conditional-propagation edges** from the partial correlation; the bottleneck node and its WIP/pressure edge; the throughput-loss outcome; the gated economic node; and the five vision defect nodes with their image counts. Engineer-declared assumptions become dashed `assumed` edges carrying author and timestamp. Returns nodes, edges, legend, linkage notice and limitations. |
| `_snapshot_metrics`, `_headroom`, `_top_bottleneck`, `_stage_notes`, `_stage_node_for*` | Internal helpers that keep the graph consistent with the bottleneck/production modules. |

---

## 7. Economics — `services/economics.py`

| Function | Purpose · logic |
|---|---|
| `assess(catalog, key, rate_card)` | With **no** rates: returns `available: false`, the reason, the missing-variable list and the Arena costing constructs that are never exported. With rates: builds line items where the **quantity comes from the dataset** (mean WIP from queue columns, output spread vs p99) and the **rate from the user**, plus explicit "cannot be evaluated" lines for scrap/rework/downtime because no such counts exist. Every line records `quantity_source` and `rate_source`. |
| `_rates_provided(rate_card)` | True when at least one rate field is set. |
| `_addressed(variable, rate_card)` | Tracks which missing variables the supplied card actually covers. |
| `_available_quantities(catalog, key)` | Measures what the dataset *can* supply (throughput, output spread, WIP, wait times) and labels each source. |
| `economics_for_scenario(catalog, payload, rate_card)` | Adds scenario-specific lines (output delta, end-of-horizon WIP holding cost) from the simulation summary, still gated on user-supplied rates. |
| `DISCLAIMER`, `MISSING_VARIABLES`, `ARENA_COSTING_NOT_EXPORTED` | The honesty constants surfaced verbatim in the API and UI. |

---

## 8. What-if simulation — `services/simulation.py`

| Function | Purpose · logic |
|---|---|
| `_validate_spec(spec)` | **Rejects honestly.** Unknown station, non-adjustable station (blanking/forklift/paint/warehouses) with the reason, missing or zero capacity delta, non-positive time factor, replications outside 1–10, non-positive demand factor or horizon — all raise `ValueError` → HTTP 400. Without this, an unsimulatable scenario would return a run identical to the baseline and read as "no effect". |
| `QueueStat` | Time-weighted queue length (integral of length over time) + observed maximum, with `set_time/enqueue/dequeue/mean`. |
| `DesRun.__init__` | Builds the station set (presses, cells, paint conveyors, quality, plus a non-comparable "cells pool" row), applies capacity overrides, and initialises the event heap. |
| `_preferred_cell(sku)` / `_choose_cell(sku)` | Routes by the **measured** SKU→cell shares (the routing conditions live in Model 3.doe and are not exported); parts wait for their calibrated cell, which is recorded as a known ambiguity. |
| `_arrive/_begin/_end/_dispatch/_duration_for` | The queue network: free server → start immediately; otherwise queue and be promoted on the next completion; then dispatch to the next documented stage. |
| `_maybe_start_qc` / `_finish_qc` | Batched quality check (batch size derived from the export) with a completion event. |
| `run()` | Schedules every part as an **arrival event** across the horizon, then drains the event heap. (The pre-pass version made every queue statistic meaningless — the regression test is in `tests/test_simulation_engine.py`.) |
| `_metrics()` | Per station: utilisation (busy-seconds / capacity×horizon), occupancy, time-weighted mean queue, max queue, completions, and a `comparable` flag. |
| `_paint_delay_seconds(catalog, parts)` | Back-solves the effective conveyor delay from the exported conveyor utilisation and documents the contradiction with the 5400 s/part in the PDF. |
| `_routing_share(catalog)` | Measures the SKU→cell split from the exported counters. |
| `_qc_batch_size(catalog)` | parts ÷ (utilisation × horizon ÷ 55 s). |
| `baseline_metrics(catalog)` | The dataset population used as the "current" column, including a capacity-weighted cells-pool row. |
| `_simulate(catalog, spec)` | Runs N replications with a fixed seed and averages them; returns stations, released/completed/WIP and the applied overrides. |
| `_comparisons(base, sim, baseline_sim)` | Builds the current-vs-simulated rows: **utilisation against the export** (validated like-for-like) and **queue metrics against an unmodified run of this engine**, each row stamped with `comparison_basis`, its explanation and the export value for context. |
| `validate(catalog, sim)` | Residual % per comparable station vs the export, worst station, mean absolute residual, verdict sentence and the known-ambiguity list. |
| `demand_scenario(catalog, spec)` | Engine B: interpolates the measured demand curve and surrogate metrics for Models 1–2. |
| `_baseline_simulation(catalog)` | Cached unmodified run, used as the queue baseline. |
| `run_scenario(catalog, spec, rates)` | Public entry point: picks the engine, runs it (cached by spec hash), attaches comparisons, validation and gated economics, and returns the advisory notice + the unavailable list. |
| `scenario_options(catalog)` | Exactly the stations the engine can adjust (with `adjustable` flags), the stations it cannot (with reasons), headroom, and the not-supported list. The UI builds its dropdown from this, so it cannot offer a control that does nothing. |

---

## 9. Vision — `services/vision_model.py` and `services/vision.py`

| Function | Purpose · logic |
|---|---|
| `vision_model.CLASSES`, `DISPLAY`, `DEFECT_CLASSES` | Fixed alphabetical class order (crack, hole, normal, rust, scratch); display labels; defect = everything except normal. |
| `split_of(name)` | Deterministic stratification: every 6th file per class is validation. Reproducible metrics. |
| `build_cache(force, size)` | Decodes the archive once into a uint8 array (96×96) + JSON metadata (mean 0.402, std 0.146, counts, ZIP fingerprint) + the entry list. Rebuilt only when the archive changes. |
| `load_cache()` | Returns the array (memory-mapped), labels and metadata. |
| `build_network()` | The `DefectCNN` (see `ALGORITHM_REFERENCE.md` §1); exposes `last_conv` and `pool` for Grad-CAM and the OOD embedding. |
| `train(...)` | Full training loop with flip/roll augmentation, cosine schedule, label smoothing, best-validation checkpointing, a wall-clock budget, and a classical HOG/pixel baseline for context. Writes the checkpoint and **measured** metrics. |
| `evaluate(...)` | Accuracy, macro-F1, balanced accuracy, per-class precision/recall/F1/support, confusion matrix. |
| `classical_baseline(...)` / `_histogram_baseline(...)` | HOG+logistic regression (falls back to pixels) so the CNN's numbers have a floor to compare against. |
| `vision.metrics()` / `available()` / `load_model()` | Lazy artefact loading with a clear message when no checkpoint exists. Loading never happens until an inspection is requested. |
| `vision._embedding_bank()` | 400 training embeddings (every 30th image) for the OOD distance, built once. |
| `vision._embedding_distance_to_training(model, tensor)` | `1 − max cosine similarity` to that bank; `None` when the bank or features are unavailable (the guard degrades rather than failing the prediction). |
| `vision._preprocess(bytes)` | PIL decode → grayscale → 96×96 bilinear → `[0,1]` → normalise with the artefact's stored mean/std. |
| `vision.inspect(catalog, specimen)` | Archive inspection: resolve the specimen (404-ready `KeyError`), read bytes, run the shared pipeline, attach archive provenance (class counts, split). Raises `RuntimeError` when no model is trained. |
| `vision.predict_bytes(bytes)` | The same pipeline for arbitrary bytes (external images), with provenance stating the image is **not** training data. |
| `vision._inspect_bytes(...)` | The one implementation both use: forward pass, flip pass, averaged probabilities, confidence, TTA agreement, OOD distance, uncertainty rule, Grad-CAM, model metrics and provenance. |
| `vision._attention_regions(...)` | Grad-CAM on `last_conv`, normalised, binned to an 8×8 grid, top 4 cells. Best-effort: returns `[]` on failure rather than failing the prediction. |
| `vision.vision_metrics_payload(catalog)` | Metrics + training record + the explicit "localization is not implemented" block for the UI. |
| `vision.sample_specimens(catalog, per_class)` | Deterministic specimen picker for the Inspection grid. |

## 10. External test images — `services/external_images.py`

| Function | Purpose · logic |
|---|---|
| `EXTERNAL_DIR` (`data/external_test`) | **The test-only store.** Not on any training path; `vision_model.build_cache()` reads `train.zip` only. `MAX_UPLOAD_BYTES` = 10 MB, `MAX_EXTERNAL_IMAGES` = 500. |
| `_sniff_format(data)` | Accepts only PNG/JPEG by **magic bytes** — a `.png` filename or a client `Content-Type` proves nothing. Raises `ExternalImageError`. |
| `_validate_and_normalize(data, max_side)` | Sniffs, checks the PNG terminator, decodes with PIL (proving it is a complete image), records dimensions/mode, and downscales anything larger than 512 px (to a PNG) so the store stays light and inference stays fast. |
| `store_upload(data, original_name, note)` | Validates, then stores under a **server-generated** `upload_<uuid>.png` and records provenance, including `training_data: false`. Hostile filenames are reduced to a basename for the record and never used as a path. Prunes to the cap. |
| `generate_test_images(catalog, kind, count, seed)` | The generator: `variation` (real archive image + brightness/contrast/noise/flip/rotation/blur, effect recorded in the description), `nonfactory` (drawn face/car/tree/phone/random noise — explicitly synthetic), `mixed` (alternating). Deterministic for a given seed; each image is stored with `training_data: false`; one failed draw is skipped, never fatal. |
| `list_images(source)` / `get_entry(id)` / `image_bytes(id)` / `delete_image(id)` | Index-backed accessors; unknown ids return `None`/`False` so the API can answer 404 cleanly. |
| `predict_external(catalog, image_id)` | Runs `vision.predict_bytes` on the stored file and adds the `external` block: id, source, note, `training_data: false`, and the statement **"External Test Image - Not Used for Training"**. The prediction logic is the same code path as archive inspections. |
| `_prune_to_limit(entries)` | Removes oldest *generated* images first, then oldest uploads, keeping the store bounded. |
| `_delete_file(filename)` | Best-effort unlink; logs rather than raising. |

## 11. AI client — `services/ai_client.py`

| Function | Purpose · logic |
|---|---|
| `SYSTEM_PROMPT` | The five hard rules: use only supplied numbers, never claim causation, say when something is unavailable, carry the limitations, respond with one JSON object of the exact contract shape. |
| `calls_last_hour()` / `status()` / `_allowed()` | Sliding-window rate limiter and a status payload that explains **why** the LLM is or is not in use. |
| `_compact(payload)` | Shrinks the evidence bundle (≤40 keys/dict, ≤8 list items, depth ≤4, floats rounded) before it reaches a provider. |
| `_build_messages(topic, evidence, question)` | Topic-specific instruction + the compacted evidence as JSON + the question, with the system contract. |
| `_post_openai(messages)` / `_post_anthropic(messages)` | Provider calls over `httpx`. OpenAI-compatible covers OpenAI, Groq, Azure, OpenRouter, Ollama, vLLM, LM Studio; Anthropic has its own body/headers. |
| `_extract_json(text)` | Pulls the JSON object out of fenced or chatty output. Returns `None` for unparseable text (which triggers the fallback). |
| `_log_call(...)` | Writes an `AICallLog` row (ok, latency, prompt chars, error) so usage stays visible; never raises. |
| `deterministic_finding(topic, evidence)` | The no-network engine: per-topic fixed-sentence narrative built **only** from values present in the evidence bundle, carrying the bundle's limitations and confidence. Validated by the same `AIFinding` contract. |
| `narrative(topic, subject_id, evidence, question, use_cache)` | **Public entry point.** Cache lookup (memory, then the `Narrative` table by evidence hash) → LLM attempt when allowed → `AIFinding` validation → fallback on any failure with the reason recorded. **Never raises.** When a provider call fails, `fallback_reason` now reports the provider error (fixed in this audit). |
| `_load_stored` / `_store` | Narrative cache persistence, best-effort. |
| `recent_logs(limit)` / `usage_summary()` | Log listing and aggregate usage (succeeded/failed, mean latency, rate-limit state). |

## 12. Routers

| Router | Key handlers (full table in `API_REFERENCE.md`) |
|---|---|
| `routers/datasets.py` | `overview`, `status`, `process`, `models`, `columns`, `quality`, `linkage`, `design`, `specimens`. |
| `routers/analytics.py` | production snapshot/bottlenecks/anomalies/regimes/demand-response/calibration/association; diagnostics cases/case/propagation/stations; economics GET+POST+requirements. `_check(key)` turns an unknown dataset into 404. |
| `routers/inspection.py` | metrics, samples, image, thumbnail, predict. Missing specimen → 404; missing model → 409. |
| `routers/external_images.py` | info, list, upload, generate, image, delete, predict. Invalid upload → 400 with a plain-language reason. |
| `routers/simulation.py` | options, run (400 on an invalid scenario, 409 when a dataset is missing), history, validation. |
| `routers/ops.py` | feedback POST/GET, review summary, assumptions GET/POST/DELETE, AI status/narrative/ask/logs. `_overview_evidence`, `_case_evidence`, `_station_evidence` assemble the bundles the LLM may see. |

## 13. Frontend — `frontend/src`

| File | Purpose |
|---|---|
| `lib/api.ts` | The single typed client. `request()` normalises every error into an `ApiError` carrying the server's `detail`, so no page ever renders a raw stack trace. `qs()` builds query strings. Includes the external-image endpoints and `ExternalImageEntry`. |
| `lib/hooks.ts` | `useApi(fetcher, deps, pollMs)` — loading/error/data + `reload()`, with cancellation and an optional poll; plus `num/int/pct/frac/signed/money` formatters that render `—` for null instead of `0` or `NaN`. |
| `components/Layout.tsx` | Sidebar navigation (Overview · Inspect · Investigate · Production · Problem Flow · What-If · AI Help · Review), dataset-pipeline status with progress, AI-mode badge, and the advisory-only notice. Polls `/api/datasets/status` while the catalog builds. |
| `components/ui.tsx` | `Card`, `Stat`, `Badge`, `Spinner`, `ErrorBox` (with retry), `Empty`, `GateNotice`, `Bullets`, `EvidenceList`, `Meter`, `UtilBar`, `KeyValue`, `FeedbackBar` (confirm/reject/needs-review with the retraining disclaimer). |
| `components/Glossary.tsx` | `Term` — hover/focus tooltip giving the plain-language meaning of a technical term (utilisation, WIP, TTA, Grad-CAM, PCA, Mahalanobis, Cohen's d, …), with the technical definition beneath. |
| `components/PropagationGraph.tsx` | React Flow graph with a hand-authored layout (route row, consequence row above, vision row below), observed/assumed/unavailable styling, a legend, and a node-detail panel driven by `onNodeClick`. |
| `components/Timeline.tsx` | The route-ordered divergence rail; prints the "no timestamps, this is route order" notice every time it renders. |
| `components/charts.tsx` | Recharts wrappers (utilisation bars, queue bars, demand curves, current-vs-simulated comparison, scatter, validation residuals with ±15 % reference lines). |
| `pages/Dashboard.tsx` | Overview: hero + status light + one plain-language main finding + the gated economics notice + "How this works". |
| `pages/Inspection.tsx` | Two tabs: **Factory images** (archive specimens) and **Test with a new image** (upload, generate, list, delete, analyse) with the external-not-training banner and the OOD warning. |
| `pages/Forensic.tsx` | Case picker → first divergence, ordered timeline, evidence list, limitations, feedback bar. |
| `pages/Production.tsx` | Throughput/WIP, utilisation chart, anomaly card, ranked bottleneck table with evidence and weights. |
| `pages/Propagation.tsx` | Wraps the graph, states the flow and the limitations. |
| `pages/WhatIf.tsx` | Station + change + amount → run → current vs simulated, economic gate, validation block. |
| `pages/Investigator.tsx` | Question box + examples → AI answer with source badge (LLM vs deterministic engine), confidence, evidence, recommendation. |
| `pages/Review.tsx` | Stored decisions with counts and the expandable payload each decision was made on. |
| `pages/Datasets.tsx` | Provenance, per-column schema table, data-quality issues, linkage analysis, MATLAB design panel. |

---

## 14. Scripts and tests

| File | Purpose |
|---|---|
| `training/train_vision.py` | CLI: build the cache, train, print **measured** metrics, write the checkpoint + metrics. |
| `scripts/inspect_datasets.py` | Reproduces every measurement in `DATASET_SCHEMA.md` straight from the raw files; `--json` writes `data/cache/dataset_report.json`. |
| `scripts/smoke_test_api.py` | 151 assertions against a live server: every endpoint, response shape, gating behaviour, hostile scenario parameters, error paths. |
| `tests/conftest.py` | Puts `backend/` on `sys.path`; builds the catalog once per session; skips (not fails) when the archives are absent. |
| `tests/test_honesty_contract.py` | Asserts the system refuses what the data cannot support: no join key, economics with no total, scenario rejections, the AI contract and its sanitiser, causal caveats, gated economic nodes. |
| `tests/test_simulation_engine.py` | Guards the engine: no queue clock ever steps backwards, no negative/oversized queue means, queue max ≥ mean, arrivals not front-loaded, validation residuals within 5 % for the consistent stations and **expected to exceed it for Cell 4**, comparisons never mix baselines. |
| `tests/test_imagery_concurrency.py` | Guards the archive read path: concurrent member reads and thumbnails never corrupt, one ZipFile handle only, atomic thumbnail writes. |
| `tests/test_external_images.py` | Guards the external-image feature: training separation, upload validation (magic bytes, truncation, oversize, empty, hostile filename), generation, prediction contract, and the OOD guard on both sides of its threshold. |
| `tests/test_ai_fallback.py` | Guards the AI layer: deterministic fallback, grounding, invalid-output fallback, provider-failure fallback with a stated reason, compaction, JSON extraction. |
