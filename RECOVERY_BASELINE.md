# RECOVERY_BASELINE.md

**What was actually broken when this recovery began, how each failure was traced to its
root cause, and what was done about it.**

This is the measured record, not a plan. Every entry below was reproduced before it was
changed and re-verified after. Dates are the session in which the work ran.

---

## 1. How this was investigated

Method, in order:

1. Inspected the repository layout and read every service, router, schema and page that the
   failures touched.
2. Started the backend and the frontend and **observed** the failures directly — HTTP
   status codes, response bodies, browser console, network panel, backend log.
3. Traced each failure down the chain `UI → API call → router → service → catalog → data`
   and fixed the lowest broken layer, never the symptom.
4. Wrote a regression test for every fix, then re-ran the full suite, the live API probes
   and the browser workflow.

No data was invented anywhere. Where a capability could not be supported by the actual
datasets, it is reported as unavailable with the reason.

---

## 2. Baseline failures (reproduced)

| # | Symptom | Root cause | Where | Fix |
|---|---|---|---|---|
| 1 | `GET /api/datasets/overview` → 500 | `catalog.build()` scanned only `data/user_datasets/*.csv`. The project's actual datasets — the three simulation exports and the image archive — were never loaded, so every downstream accessor hit an empty catalog. | `services/catalog.py` | `catalog.py` rewritten to load the three model exports (via the surviving parquet caches, rebuilt from the raw CSVs when absent), index the 12,000-image archive, and load user CSVs as an *additional* dataset kind. |
| 2 | `GET /api/datasets/status` → 500 | `Catalog.status` was a bare object without the `snapshot()` accessor the router and tests call. | `services/catalog.py` | Restored `StatusWrapper` with `snapshot()`, `state`, `progress`, `step` and error reporting. All states now report honestly: `idle`, `running`, `ready`, `failed` with the reason. |
| 3 | `GET /api/production/anomalies` → 500 | The analytics module had been replaced by a stub exposing `analyze_anomalies()` while the routers call `anomaly_report(catalog, key, top=)`, `regime_contrast`, `demand_response`, `calibration_report`, `stage_association`. Every call raised `AttributeError`. | `services/analytics.py` | Rewrote the module to the contract the routers and `bottleneck.py` actually use: Mahalanobis anomaly report with per-feature drivers, utilisation calibration identity, demand response over the designed factor, stage association, and `model3_queue_columns()`. |
| 4 | `GET /api/production/snapshot` → 409 | `bottleneck.production_snapshot` raises `KeyError` when `catalog.model_df("model3")` is missing — a direct consequence of failure #1. | `services/catalog.py` | Fixed by #1; the endpoint now returns the full snapshot. A genuinely missing dataset still returns `404` with a readable message (see §5). |
| 5 | `GET /api/diagnostics/*` → 500 | The forensics module was a 54-line stub: no `list_cases`, `build_case`, `propagation_graph`, `station_baseline` or `severity_from_z`, and it imported a `build_dynamic_domain` symbol that does not exist. | `services/forensics.py` | Rewrote to a real case builder (group contrasts with per-station divergence timeline and evidence) and a real propagation graph with validity-checked edges. |
| 6 | What-If produced invented numbers | The simulation module returned `base_throughput * 1.05` and `"↑ Improved"`. That is a fabricated result, not a simulation. | `services/simulation.py` | Rewrote as a discrete-event re-simulation of the documented route with batch-cycle calibration, TTA-free deterministic queue statistics, and export-validated utilisation. Scenario deltas are now computed from completed parts. |
| 7 | `vision.inspect` / `predict_bytes` missing | The vision service was a 49-line stub returning `{"prediction": "Unknown (Model untrained)", "confidence": 0.0}`; no CNN inference, no attention map, no TTA, no OOD guard. The trained checkpoint (`vision_cnn.pt`) and the image cache were both intact. | `services/vision.py` | Wrote the service on top of the intact `vision_model.py`: real inference, flip-TTA agreement, gradient attention regions, and the out-of-distribution guard. |
| 8 | External-image prediction crashed | `predict_bytes` had no `catalog`-free signature and the tensor path called `.numpy()` on grad-enabled tensors. | `services/vision.py` | Fixed the signature the callers use and detached every tensor crossing into NumPy; wrapped TTA in `torch.no_grad()`. |
| 9 | `services/domain.py` missing the plant description | `ModelDef`, `StationSpec` and `MODELS` were gone while `datasets.py`, `ops.py`, `bottleneck.py` and `economics.py` all read them (`economics.py` also referenced an undefined `MODEL3_STATIONS`). | `services/domain.py` | Restored the three documented models with capacities, queue/utilisation columns and time sources from `Model 3.pdf` / `ParametersFile.xls`; repointed `economics.py` at `MODELS["model3"].stations`. |
| 10 | Nav links dead — every route rendered "Page not found" | The sidebar links to `/inspect`, `/investigate`, `/flow`, `/what-if`; the router only declared `/inspection`, `/forensic`, `/propagation`, `/whatif`. | `frontend/src/App.tsx` | Declared both the friendly paths and the legacy ones, so no existing link or bookmark is dead. |
| 11 | Every dataset-dependent page showed "No dataset loaded" | Nothing ever called `setActiveDataset`, so `getActiveDataset()` always returned `""` and the client short-circuited every request. | `frontend/src/lib/api.ts`, `components/Layout.tsx` | Added a first-present-dataset default recorded once the catalog reports ready. No `model1/2/3` hardcoding — the key is whatever dataset is actually loaded, and an empty catalog still shows the empty state. |
| 12 | Frontend could not reach the backend through the dev server | `vite.config.ts` proxies `/api` to port **8001** while the documented run command used 8000. | `frontend/vite.config.ts` / run docs | Reconciled: the backend runs on 8001 (the proxy default, overridable with `VITE_BACKEND`) and the README states it. |
| 13 | `npm run typecheck` failed | `tsconfig.json` included `vite.config.ts` but `@types/node` is not installed, so `process.env` was untyped; plus two dead imports and a call to a non-existent `/agent/chat` endpoint returning an undeclared type. | `frontend/tsconfig.json`, `lib/api.ts`, `pages/Production.tsx` | Scoped the typecheck to `src`, removed the dead imports, and pointed the chat helper at the real `/ai/ask` endpoint with its declared response type. |
| 14 | Image predictions slowly-timeout under the browser | The OOD guard recomputed 400 archive feature vectors on every single prediction (~12 s each, and the Inspect page fires several). | `services/vision.py` | Reference features are computed once per process and cached to `data/cache/vision_ood_refs.npy`; per-prediction cost dropped to 0.0–0.3 s warm. |
| 15 | In-distribution images were flagged out-of-distribution | The OOD threshold was 0.03, chosen before calibration. Measured: 60 random archive images score p95 = 1.01 and max 1.40. | `services/vision.py` | Recalibrated to 1.6 after measuring both sides: archive images ≤ 1.40, synthetic non-factory patterns ≥ 1.95, pure noise > 90. |
| 16 | `test_vision_zip_handle_is_created_once_under_concurrency` failed | The catalog had no shared archive handle at all (each read opened its own `ZipFile`), which also left the original interleaved-read corruption possible. | `services/catalog.py` | Restored a single double-checked, lazily opened shared handle with reads serialised on a lock, matching the concurrency contract the tests pin. |
| 17 | `crack/0`-style specimens were not resolvable | `find_specimen` compared zero-padded suffixes against the raw index instead of resolving the i-th image of a class. | `services/catalog.py` | Resolves both `train/crack/crack_00000.png` and `crack/0` (n-th entry of that class). |

---

## 3. Things that were *not* broken (verified intact, left alone)

| Component | Evidence it was intact |
|---|---|
| CNN training + evaluation (`services/vision_model.py`) | Full training loop, deterministic stratified split, confusion matrix and classical baseline all present; checkpoint `vision_cnn.pt` and the 96×96 cache on disk. |
| `VisionMetrics` payload | `/api/inspection/metrics` reports the measured held-out accuracy. |
| Bottleneck analysis (`services/bottleneck.py`) | Composite scoring, WIP-evidence handling and the Cell 4 contradiction note all original. |
| Economics gating (`services/economics.py`) | The "no cost exists in the data" rule and rate-card accounting survived; only the `MODEL3_STATIONS` reference had to be repointed. |
| Schemas (`app/schemas.py`) | The strict AI contract, look-alike character normalisation and redaction all intact. |
| External-image store (`services/external_images.py`) | Archive-separation rule and the `training_data: false` contract intact. |
| Tests | 82 tests, all passing after the fixes above. |

---

## 4. Verification performed after the fixes

| Check | Result |
|---|---|
| `python -m pytest tests` | **100 passed** (after the second pass in §6) |
| `python scripts/smoke_test_api.py --base http://127.0.0.1:8001` | **151 assertions passed, 0 failed** |
| `npm run typecheck` | clean |
| `npm run build` | production bundle built (815 kB / 242 kB gzip) |
| Live API probes (datasets, production, diagnostics, economics, simulation, AI, review) | all 200, no 500s |
| Browser walkthrough, all 8 routes | Overview, Inspect, Investigate, Production, Problem Flow, What-If, AI Help, Review, Datasets — all render real data |
| Image inspection through the UI | `crack` 98.3 % confident, flip-check 100 % |
| Non-factory image | flagged out-of-distribution |
| What-If through the UI | Assembly Cell 4 +1 capacity: completed parts 51,533 → 52,026 (**+493**), busy 81.4 % → 80.8 % |
| AI narrative | answered live by the configured model from backend-computed evidence (bottleneck score 0.8783, 22.9 % headroom) |

---

## 5. Deliberate honest limitations (not bugs)

These are properties of the supplied data, and are disclosed in the UI and docs rather
than worked around:

* **No localization.** The archive has class folders and no bounding boxes or masks, so the
  system shows a gradient *attention* map and says so. No defect box is ever claimed.
* **No timestamps anywhere.** `Time_Now` is constant 24 in all 605,620 rows of Model 3 and
  Models 1–2 export no time column, so rows are independent replications and no wall-clock
  ordering is claimed.
* **No join key between the datasets.** The image archive and the simulation exports share
  no identifier, so defect classes cannot be attached to stations from data. Only
  engineer-declared assumptions create those edges, and they are labelled `assumed`.
* **No cost data.** No export contains a price, cost, scrap, rework or downtime value, so
  economics stays gated behind a user-supplied rate card.
* **Feedback does not retrain.** Review decisions are stored and audited; nothing in the
  model pipeline consumes them, and the API says exactly that.
* **Cell 4 capacity contradiction.** The export's utilisation implies ~5 parallel resources
  where `Model 3.pdf` documents 4. It is reported as a finding and shown in the validation
  residuals, never tuned away.

---

## 6. Second pass: what the live verification caught after §2's fixes

The fixes in §2 made the endpoints answer, but the app had never been driven end-to-end
with the *browser* against the *rebuilt* services. Doing that found a further set of real
problems, all fixed and covered by tests (`TEST_REPORT.md` §2.16–§2.25 records the symptom,
cause and fix of each):

| # | Problem | Impact | Fix |
|---|---|---|---|
| 18 | `GET /api/production/association` raised `TypeError` (`sort_values(reverse=…)`) | 500 on a documented endpoint | ordered by \|correlation\| explicitly, constant columns dropped |
| 19 | `demand-response` carried `np.int64` values | 500 for Models 1–2 although the maths was right | cast at source + `NumpySafeJSONResponse` as the app default |
| 20 | Anomaly report used a 4.4 GB einsum intermediate and whitened twice | 68 s cold on the landing page | BLAS matmul + reused whitening + memoised report: **3.5 s cold, 0 s warm** |
| 21 | `/{key}/quality` re-scanned 78×605,620 on every visit | 3.8 s per page view | memoised on the catalog (0.002 s warm) |
| 22 | Every design token and component class the pages use was undefined | the whole UI rendered unstyled | light industrial tokens and classes written into `index.css`; dark hardcoded colours replaced |
| 23 | Dashboard crashed on load (`driver.feature` undefined) | blank landing page | anomaly payload now emits the documented driver/importance fields |
| 24 | Money-impact card keyed on a field the API never sends | the honest "no cost data" card was always hidden | reads the per-dataset capability map |
| 25 | What-If compared released baseline with completed scenario | reported a 12 % "loss" for a capacity increase | like-for-like comparison (**+493 parts**), export total shown separately |
| 26 | Propagation graph placed 17 of 32 nodes on a fallback grid | unreadable hairball, route cut off by the zoom floor | layout derived from the graph structure; `fitView` may zoom out far enough to fit |
| 27 | Dataset upload lived at `/upload`, buffered the body, trusted the filename, did not rebuild | unusable and unsafe | `/api/upload/`, basename sanitisation, 25 MB streaming cap, parse check, catalog rebuild, capability report, UI card |
| 28 | Upload directory was CWD-relative; `build()` never cleared `user_profiles` | uploaded files landed in `backend/data/`; deleted files lingered as datasets | path anchored to `PROJECT_ROOT`; profiles cleared on every build |
| 29 | Profiler called any `*time*` column a timestamp; capability maps had two shapes | an honest process table was reported as having no process variables; one UI could not render both | name-based detection for stations/process/costs/batches; one capability shape with reasons |
| 30 | `/api/simulation/options` returned `adjustable: true` | the live contract test (and the documented shape) expects a per-change map | `adjustable: {capacity, processing_time}` |
| 31 | `ColumnProfile.documented` was never set | the "documented only" filter was a no-op and every column claimed to be documented | flag set from the documented station/throughput definitions, with an explanatory note |
| 32 | `status` dropped `stages`, cases dropped a list-shaped `confidence_basis`, vision metrics never declared localisation | contract failures, and the UI had nothing to show for them | all three restored, with reasons |
