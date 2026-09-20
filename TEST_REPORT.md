# TEST_REPORT.md

What was actually run, what it covers, and what it caught.
Every number here was produced by running the suites in this checkout; nothing is estimated.

Environment: Windows, Python 3.12, Node 22. Backend served with
`python -m uvicorn app.main:app --port 8001` from `backend/`, frontend with
`npm run dev` (Vite, port 5173, proxying `/api` to 8001) and verified in the built
bundle too.

---

## 1. Summary

| Suite | Command | Result |
|---|---|---|
| Unit / contract tests | `python -m pytest tests -q` | **100 passed, 0 failed** (35 s) |
| Live API smoke test | `python scripts/smoke_test_api.py --base http://127.0.0.1:8001` | **151 assertions passed, 0 failed** |
| Frontend typecheck | `cd frontend && npm run typecheck` (`tsc -b --noEmit`) | **clean** |
| Frontend production build | `cd frontend && npm run build` | **built** (`dist/assets/index-*.js`, 815 kB / 242 kB gzip) |
| Browser walkthrough | Preview tab against the Vite dev server | **all 8 routes render real data, console clean** |
| Dataset upload through the UI | Datasets page → *Upload & profile* | **profiled, listed, capability map shown** |

Test files:

| File | Tests | Covers |
|---|---|---|
| `tests/test_honesty_contract.py` | 27 | Refusals: no invented economics, no join key claimed, causal caveats, scenario validation, propagation edges, assumption validation |
| `tests/test_external_images.py` | 24 | External test-image feature: separation from training, upload validation, size guard, prediction contract, OOD guard |
| `tests/test_ai_fallback.py` | 14 | AI layer: deterministic fallback, truncation retry, rate-limit retry, text normalisation, injection redaction, grounding |
| `tests/test_simulation_engine.py` | 10 | Discrete-event engine: arrivals, queues, capacity, scenario deltas, validation residual |
| `tests/test_imagery_concurrency.py` | 4 | Concurrent image reads/inference |
| `tests/test_api_hardening.py` | 3 | CORS configuration, 500 handler leaks no internals |
| `tests/test_upload_flow.py` | 9 | Dataset upload: basename sanitisation, size cap, parse rejection, profiler detection, capability map shape, catalog rebuild, deleted upload disappears |
| `tests/test_contract_shapes.py` | 9 | Payload contracts the UI depends on: numpy-safe JSON, status stages, documented/undocumented columns, case `confidence_basis`, vision localisation disclosure, per-change adjustable map, anomaly driver fields |

The suites are hermetic: `tests/conftest.py` forces `AI_DISABLE=true` and an empty
key before `app.config` is imported, so no test touches the network and none depends
on whether the developer happens to have a key in `.env`. Tests that need the LLM
code path enable it explicitly.

---

## 2. Bugs this testing found (and their fixes)

Each of these was reproduced first, then fixed, then re-verified.

### 2.1 AI narrative silently fell back to the template engine — **fixed**

* **Symptom.** `/api/ai/narrative` returned credible text, but `source` was
  `deterministic` and `fallback_reason` said `response was not valid JSON`.
* **Cause.** The provider stopped at `finish_reason=length`: the model is
  reasoning-capable, so part of the 900-token budget went to hidden reasoning and
  the JSON reply was cut off mid-object.
* **Fix.** `_call_provider` now returns a `Completion(content, finish_reason)`; when
  the JSON is unparseable *and* the model was truncated, the client retries once with
  double the budget (capped at 4000 tokens) before falling back. Default
  `AI_MAX_OUTPUT_TOKENS` raised 900 → 1200.
* **Verification.** Live `topic=scenario` and `topic=station` calls now report
  `source=llm`; `test_truncated_json_is_retried_with_a_larger_budget` and
  `test_permanent_truncation_falls_back_with_a_truncation_reason` cover both branches.

### 2.2 A 429 from the provider discarded the answer — **fixed**

* **Symptom.** Under normal audit traffic, Groq returned `429 Too Many Requests` and
  the app answered from the template engine.
* **Fix.** `_request_json` retries up to three times on 429/500/502/503/504,
  honouring `Retry-After` and capping the wait at 10 s so the UI never hangs.
* **Verification.** Log shows `AI provider returned 429; retrying in 3.0s` followed by
  `200 OK`; `test_rate_limited_call_is_retried_then_succeeds` and
  `test_rate_limit_backoff_is_capped`.

### 2.3 The propagation graph could contain a dangling edge — **fixed**

* **Symptom.** The smoke test failed on `every edge references real nodes`:
  `defect_crazing -> stage_blanking` pointed at a node that does not exist.
* **Cause.** `POST /api/review/assumptions` stored any `defect_class` string, and the
  graph built the edge without checking the class is one of the archive's five labels.
* **Fix.** Write side: the endpoint validates both values against the datasets and
  returns 422 listing the valid ones. Read side: the graph skips (and counts)
  assumptions whose endpoints do not exist, and reports them under
  `skipped_assumptions`.
* **Verification.** Live: bad class → 422, bad station → 422, valid → 200 with 0
  dangling edges. Four tests added in `test_honesty_contract.py`.

### 2.4 An unknown station silently became the Assembly node — **fixed**

* **Cause.** `_stage_node_for()` ended with `.get(stage, "stage_assembly")`, so any
  unrecognised stage name was drawn as Assembly.
* **Fix.** Unknown stages now return `None` and no edge is created; the conditional
  correlation loop also skips edges whose endpoints do not resolve.

### 2.5 AI text carried invisible characters — **fixed**

* **Symptom.** The model wrote "Blanking" with a **soft hyphen** (U+00AD), so the
  station name no longer matched the dataset label anywhere the UI looks a name up.
* **Fix.** `_normalise_text()` drops zero-width/invisible characters and maps
  look-alike hyphens and spaces to ASCII before validation.
* **Verification.** Live response contains no U+00AD/U+2011; two tests assert this.

### 2.6 The injection redaction for `;--` never matched — **fixed**

* **Cause.** `\b(drop\s+table|delete\s+from|;--|rm\s+-rf)\b` — a word boundary after
  `;--` can never match, so that branch was dead code.
* **Fix.** Word boundaries are applied per alternative. Covered by
  `test_command_like_model_output_is_redacted`.

### 2.7 Upload cap was enforced after buffering the whole body — **fixed**

* **Cause.** `await file.read()` held the entire upload in memory, then the service
  rejected anything over 10 MB.
* **Fix.** `_read_capped()` checks the declared size first and otherwise reads in
  256 kB chunks, stopping as soon as the cap is passed (HTTP 413 with a friendly
  message). The frontend also refuses an oversized file locally, using the limit the
  API advertises.
* **Verification.** Live: an 11 MB upload → `413 This image is 11.0 MB, which is above
  the 10 MB limit…`; an 8.4 MB image still uploads. Three tests cover the guard.

### 2.8 Two "failure path" tests were passing vacuously — **fixed**

* **Cause.** `Settings` is a frozen dataclass whose defaults are evaluated once at
  class-definition time, so constructing a new `Settings()` does not re-read the
  environment. The test helper that "enabled the LLM" therefore never did.
* **Fix.** The helper uses `dataclasses.replace(...)` and asserts the variant is
  really enabled. Those tests now exercise the failure paths they name.

### 2.9 Raw FastAPI validation JSON was shown to the user — **fixed**

* **Symptom.** A rejected scenario rendered
  `[{"type":"literal_error","loc":["body","spec","kind"],…}]` in the UI.
* **Fix.** `lib/api.ts` now formats any error into one sentence: the backend's own
  human-written `detail` strings pass through, validation arrays are summarised per
  field, and status codes map to plain language. A dropped connection says
  "Could not reach the analysis service…" instead of a browser `TypeError`.

### 2.10 CORS failed open on a blank setting — **fixed**

* **Cause.** `allow_origins=list(settings.cors_origins) or ["*"]`: clearing the
  variable silently allowed every origin.
* **Fix.** Empty now means no cross-origin access, with a startup warning, and the
  allowed methods/headers are narrowed to what the app serves. Verified live: the
  configured dev origin receives `access-control-allow-origin`, an unlisted origin
  receives no header.

### 2.11 The 500 handler leaked exception text — **fixed**

* **Cause.** `{"detail": f"internal error: {type(exc).__name__}: {exc}"}` returned
  internal paths and library internals to the client.
* **Fix.** The traceback goes to the log with an 8-character reference; the client
  gets a sentence containing that reference. Covered by
  `test_unhandled_error_returns_no_internals_to_the_client`.

### 2.12 Frontend: duplicate out-of-distribution warning — **fixed**

The `out_of_distribution` block was rendered twice in the inspection result panel,
so the warning appeared twice. Removed the duplicate.

### 2.13 Frontend: propagation graph was unreadably zoomed out — **fixed**

`fitView` shrank the wide route to ~0.29× to fit a narrow column, making every label
illegible. The floor is now 0.45 with panning, a hint line, the page widened to
`max-w-7xl`, and the view refits on container resize.

### 2.14 Frontend: an unknown URL silently showed the Dashboard — **fixed**

The catch-all route rendered `Dashboard`, so a mistyped link displayed the wrong page.
It now renders a "Page not found" screen with links to the real views.

### 2.15 Frontend: React Router warnings on every page — **fixed**

The v7 future flags are enabled, so the console is free of deprecation warnings —
which is what makes "console is clean" a usable check.

### 2.16 `GET /api/production/association` → 500 — **fixed**

* **Symptom.** 500 on the association endpoint.
* **Cause.** `Series.sort_values(..., reverse=True)` — no such argument. Also correlated
  constant columns, which made numpy print a divide warning.
* **Fix.** Order by `|correlation|` explicitly, drop constant columns before `corrwith`,
  and return an honest `unavailable` for a dataset with no derived output column.

### 2.17 `/api/production/demand-response` → 500 for Models 1 and 2 — **fixed**

* **Symptom.** Model 3 answered `unavailable` (correctly), Models 1–2 returned 500.
* **Cause.** The payload carried `np.int64` demand levels, which the default JSON encoder
  refuses — a successful computation turned into a server error.
* **Fix.** Levels are cast to `int` at the source, and the application now uses a
  `NumpySafeJSONResponse` as its default response class (`app/json_utils.py`), so a stray
  numpy scalar can never break an endpoint again. Covered by `test_contract_shapes.py`.

### 2.18 The anomaly report took 68 s and the landing page waited for it — **fixed**

* **Symptom.** `GET /api/production/anomalies?key=model3` needed 68 s cold.
* **Cause.** Two compounding problems: `np.einsum("ij,jk,ik->i", diff, inv, diff)`
  materialises an (n, k, k) intermediate — 4.4 GB at 605,620 rows — instead of using a
  BLAS matmul; and the whitening ran twice per report (`_feature_importance` recomputed
  it), with a third cost from re-selecting every column from the full frame by label
  (~32 s on its own).
* **Fix.** `projected = diff @ inv` then a row-wise dot; the whitened matrix travels with
  the scores so importance reuses it; and the finished report is memoised on the catalog
  (`analysis_cache`, cleared on every build).
* **Verification.** 68 s → 3.5 s cold, 0.000 s warm. The whole test suite dropped from
  74 s to 36 s as a side effect.

### 2.19 `/api/datasets/model3/quality` recomputed a 4 s scan on every visit — **fixed**

`duplicated()` + `nunique()` over 78 columns × 605,620 rows cannot change without a
rebuild, so the report is memoised on the catalog: 3.8 s → 0.002 s warm.

### 2.20 The UI had no styling: every design token was undefined — **fixed**

* **Symptom.** Panels, chips, inputs, tables and the graph were unstyled; the sidebar and
  header were dark with dark text.
* **Cause.** `index.css` had been replaced by a light `@theme` palette (`--color-primary`,
  `--color-surface`, ...) while every component still used a *different* set of names —
  `--color-ink`, `--color-ink-dim`, `--color-ink-faint`, `--color-edge`, `--color-accent`,
  `--color-ok/warn/bad`, `--color-panel`, `--color-hull`, and the classes `.panel`,
  `.panel-flat`, `.panel-head`, `.panel-title`, `.chip-*`, `.field`, `.mono`, `.table`,
  `.btn-*`, `.flow-node`, `.fade-in`. None of them existed anywhere in the source or the
  built CSS (checked with `getComputedStyle`: every variable reported `UNDEFINED`).
* **Fix.** `index.css` now defines the tokens and all of those classes in the light
  industrial palette (warm white, charcoal ink, slate-blue accent), plus `.callout-*` for
  the inline notices, and the components that hardcoded dark rgba/hex values (sidebar,
  header, error box, gated notice, timeline bar, graph canvas, minimap, node tints) were
  moved onto the theme.

### 2.21 The landing page crashed to a blank screen — **fixed**

* **Symptom.** `TypeError: Cannot read properties of undefined (reading 'replace')` in
  `Dashboard`, leaving an empty page.
* **Cause.** The dashboard renders the top anomaly's drivers as `driver.feature`, but the
  restored service returned `{column, value, z}` only. `top[].parts`, `anomalous_runs` and
  `feature_importance[].feature/.loading` were missing too, although the client types and
  `FUNCTION_REFERENCE.md` document them.
* **Fix.** The anomaly report now emits the documented names (keeping `column` for flat
  consumers) and `test_contract_shapes.py` pins every field.

### 2.22 The money-impact card could never appear — **fixed**

`Dashboard` gated the card on `overview.capabilities.economic_impact`, a field the API has
never sent (`capabilities` is per-dataset: `{available, reasons}`). The value was
`undefined`, so the honest "no costs in the data" card was permanently hidden. It now
reads the active dataset's capability map and also shows the reason.

### 2.23 What-If compared two different quantities and reported a 12 % "loss" — **fixed**

* **Symptom.** Raising Assembly Cell 4 capacity by one showed production "59,363 → 52,026".
* **Cause.** The baseline figure was `baseline_released_parts` (parts *released into the
  line*, the export's own total) and the scenario figure was `completed_parts` (parts that
  finished the whole route in the re-simulation). Comparing them is meaningless; the
  engine's own like-for-like delta was **+493 parts**.
* **Fix.** The card compares completed vs completed, states the delta explicitly, and shows
  the exported released total separately so the number is still visible.

### 2.24 The propagation graph placed 17 of its 32 nodes on a fallback grid — **fixed**

The frontend laid the graph out from a hand-written map of node ids (`stage_logistics`,
`state_storage`, `outcome_economic`, ...) that had drifted from what the API returns
(`process_problem`, `stage_*`, `wip_*`, `defect_*`, `production_outcome`,
`economic_impact`). The unplaced majority spilled into a grid below, which is what made the
diagram a hairball. The layout is now derived from the graph's own structure: the route is
the `feeds` chain laid out left to right, a node fed by `queues_parts_into` hangs under its
station, `outcome`/`economic` nodes sit above, `defect` nodes on their own row, and only
anything truly unexplained takes the fallback grid. `fitView` may also now zoom below the
old 0.45 floor so the whole route is visible instead of being cut off.

### 2.25 Uploaded datasets: the endpoint, the profiler and the capability map — **added**

`POST /api/upload/` accepts CSV uploads and closes the loop
`UPLOAD → DATASET ID → PROFILER → CAPABILITY MAP → CATALOG`, surfaced by an
*Upload your own dataset (CSV)* card on the Datasets page (verified by driving the file
input in the browser, not just by curl). What this had to fix:

* the endpoint lived at `/upload` while the client and the dev-server proxy only know
  `/api`;
* the whole body was buffered before any check — now a 25 MB streaming cap with a plain 413;
* the filename was used as given, so `../../app/main.py.csv` could write outside the upload
  directory — now reduced to its basename;
* an unparseable file was stored and only failed later inside the catalog — now rejected
  with the parser's reason;
* the catalog was not rebuilt, so a new file did not appear until a restart;
* the directory was resolved against the process CWD, so starting the server from
  `backend/` (as the docs suggest) silently looked in `backend/data/`;
* `build()` never cleared `user_profiles`, so a deleted CSV stayed in the dataset list and
  kept a stale capability map for the life of the process;
* user datasets returned a *different* capability shape (flat `{"Simulation": true}`) from
  the built-in ones (`{available, reasons}`), so one UI could not render both;
* the profiler classified any column containing "time" as a timestamp (`cycle_time_s`), so
  a perfectly ordinary process table was reported as having no process variables.

---

## 3. Workflow verification (live, in the browser)

Session of 2026-09-20, backend on 8001, Vite on 5173, all figures read from the running app.

| Step | Result |
|---|---|
| Start backend + frontend | catalog `ready` in 3.4 s, Vite in 0.58 s |
| Overview / dashboard | status "Busy in places", 12,000 images, busiest station Assembly Cell 4 at 81.4 %, main finding names Press4_Queue as unusually high, money-impact card now visible with its reason |
| Inspect → factory image | `crack` 98.3 % confident, flip check 100 %, attention highlight offered |
| Inspect → **Test with a new image** | stored and predicted; "Not Used for Training" notice; OOD guard flags a synthetic non-factory image |
| Investigate | cases load, first divergence at Forklift (3 stations affected, confidence 90 %), route-ordered timeline with the "no timestamps" caveat |
| Production | 44,665 parts/run, 35.4 parts WIP, utilisation chart, unusual run #360552 explained by its drivers |
| Problem Flow | 32 nodes, 26 edges, 0 dangling, structure-derived layout, click-through evidence, economic node gated `unavailable` |
| What-If | Assembly Cell 4 +1 capacity: completed 51,533 → 52,026 (**+493**), busy 81.4 % → 80.8 %, economics gated with its reason |
| AI Help | live LLM answer citing the bottleneck score 0.8783, 81.37 % utilisation, 22.9 % headroom and the Cell 4 capacity contradiction |
| Review | 29 stored decisions (6 confirmed, 2 rejected, 21 needs review) with timestamps; states that no retraining happens |
| Datasets | 4 datasets; an uploaded CSV appears with its capability chips and *why* each unavailable feature is unavailable |
| Dataset upload (in-browser) | file selected in the UI → "1 file(s) uploaded and profiled." → listed with its capability map |
| Dataset with missing capabilities | a 3-column notes CSV lists all six features as unavailable with reasons; no crash, no zeros, nothing invented |
| Console | no application errors on any route; only Vite connect messages |
| Backend log | no unhandled 500s; only expected 4xx from deliberate negative probes |

---

## 4. Known limitations of this test suite

* **No browser automation in CI.** The walkthrough above was performed manually through
  the Preview tab; the automated part is the API smoke test, which drives every
  endpoint with valid, missing, malformed and hostile input.
* **No frontend unit tests.** There is no test runner configured in `frontend/`; the
  client is covered by `tsc` plus the manual walkthrough.
* **Timing-sensitive numbers.** Simulation deltas depend on the fixed seed
  (`SIM_RANDOM_SEED`), so tests assert direction and consistency rather than exact
  magnitudes.
* **Vision tests skip without artefacts.** `train.zip` and a trained
  `data/cache/vision_cnn.pt` are required; without them those tests skip rather than
  fail, so a fresh clone still reports a useful result.
* **Synthetic images are not ground truth.** Nothing here measures real defect
  accuracy on genuinely new factory images; see `EXTERNAL_IMAGE_TEST_REPORT.md`.
