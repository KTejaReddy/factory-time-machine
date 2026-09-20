# FINAL_AUDIT.md

End-to-end audit of Factory Time Machine, performed against the **running** application
on 2026-09-20. Everything below was reproduced live — in the API and in the browser — not
read off the code. Where a feature cannot be supported by the supplied data it is marked as
such and the reason is stated; no figure is invented anywhere.

Companion documents: `RECOVERY_BASELINE.md` (what was broken and how each failure was
traced to its cause), `TEST_REPORT.md` (suites, counts, per-bug detail),
`SECURITY_AUDIT.md`, `EXTERNAL_IMAGE_TEST_REPORT.md`, `SYSTEM_EXPLANATION.md`,
`ALGORITHM_REFERENCE.md`, `FUNCTION_REFERENCE.md`, `API_REFERENCE.md`,
`DATASET_EXPLANATION.md`, `DATASET_SCHEMA.md`.

Verification commands used:

```bash
python -m uvicorn app.main:app --port 8001          # from backend/
cd frontend && npm run dev                          # Vite on 5173, /api proxied to 8001
python -m pytest tests -q                           # 100 passed
python scripts/smoke_test_api.py --base http://127.0.0.1:8001   # 151 passed, 0 failed
cd frontend && npm run typecheck && npm run build    # clean / built
```

---

## 1. Feature status

| Feature | Status |
|---|---|
| Application startup, backend + frontend | ✅ |
| Dataset loading (3 exports + 12,000-image archive) | ✅ |
| Dataset upload (CSV) → profiler → capability map | ✅ added and verified in the browser |
| Defect classification (CNN) | ✅ live, 5 classes |
| Confidence + flip-TTA uncertainty | ✅ |
| Out-of-distribution guard | ✅ rots/blur and non-factory images flagged |
| Defect localization | ❌ **not possible from this data** — folder labels only, no boxes or masks; the app shows a gradient attention map and says it is not a defect position |
| Process anomaly detection | ✅ PCA + Mahalanobis, 605,618 rows scored in 3.5 s |
| Root-cause evidence + route-ordered timeline | ✅ |
| Propagation graph | ✅ 32 nodes, 26 edges, 0 dangling |
| Bottleneck analysis | ✅ |
| Economics | ⚠ **correctly gated** — no export contains a cost, so it needs a rate card; it refuses to invent money |
| What-if simulation | ✅ discrete-event re-simulation, export-validated |
| AI narrative + Q&A | ✅ live LLM grounded on backend-computed evidence, deterministic fallback |
| Human feedback | ⚠ stored and audited; it does **not** retrain anything, and says so |
| External image testing | ✅ upload/generate/predict, never training data |
| Security | ⚠ two findings fixed, three residues documented in `SECURITY_AUDIT.md` |
| Automated tests | ✅ 100 unit/contract + 151 live API assertions |
| UI/UX (light industrial theme, capability display) | ✅ |
| Dataset-driven behaviour across every page | ⚠ see §3.1 |

Legend: ✅ tested and working · ⚠ partial / capability-dependent, disclosed · ❌ not
possible with this data.

---

## 2. The four required verifications

### 2.1 The original provided dataset — ✅

| Page | Observed |
|---|---|
| Overview | status light "Busy in places"; 12,000 images; busiest station Assembly Cell 4 at 81.4 %; main finding names `Press4_Queue` unusually high and `Forklift_Assembly_Queue` unusually low; money-impact card present with its reason |
| Inspect | `train/crack/crack_00000.png` → **crack**, confidence 98.3 %, flip check 100 %, verdict "Confident", attention highlight offered |
| Investigate | three forensic cases; lowest-output-1 % case → first divergence at Forklift, 3 stations affected, confidence 90 %, route-ordered timeline with the "no timestamps" caveat |
| Production | 44,665 parts/run, 35.4 parts WIP, utilisation chart, unusual run #360552 explained by its drivers, bottleneck table |
| Problem Flow | 32 nodes / 26 edges / 0 dangling, structure-derived layout, node click-through evidence, economic node gated `unavailable` |
| What-If | Assembly Cell 4 +1 capacity → completed parts **51,533 → 52,026 (+493)**, busy 81.4 % → 80.8 %, economics gated with its reason |
| AI Help | live answer citing bottleneck score 0.8783, 81.37 % utilisation, 22.9 % headroom, and the Cell 4 capacity contradiction |
| Review | 29 stored decisions (6 confirmed, 2 rejected, 21 needs review) with timestamps |
| Datasets | catalog ready in 3.4 s, 4 datasets, per-dataset capability chips, "no cross-dataset join key" measured |
| Console | no application errors on any route (only Vite connect messages) |
| Backend log | no unhandled 500s |

### 2.2 A different temporary test dataset — ✅

Two purpose-built CSVs with column names unlike the supplied exports:

* `machine_shift_export.csv` — `Machine_ID, temperature_C, queue_length, cycle_time_s,
  scrap_cost_usd, lot, shift` (240 rows);
* `browser_upload.csv` — `Station, Temperature_C, Queue, Cycle_Time, Scrap_Cost, Batch`
  (3 rows), selected in the browser's own file input.

Both were uploaded through the app's *Upload & profile* control → *"1 file(s) uploaded and
profiled."* → listed with their capability map, and `machine_shift_export` was correctly
detected as having stations, three process variables, a cost column and a lot identifier:
`✓production ✓anomaly ✓forensics ✓economics`, `⚠vision ⚠simulation` with reasons. The same
behaviour was confirmed through the API: `anomaly_report` on the uploaded key scored all 240
rows and returned `success`. (The temporary CSVs were removed after the verification and the
catalog rebuilt, so the shipped checkout lists only the supplied datasets.)

### 2.3 An external image — ✅

A synthetic non-factory image and blur/rotation/level variants are stored under
`data/external_test/` (never training data, `training_data: false` in every response).
The archive-independent path was exercised live: a non-factory pattern is flagged
out-of-distribution (distance well above the recalibrated 1.6 threshold) while archive
images score ≤ 1.40, so the guard flags the unfamiliar without crying wolf on the familiar.
Full detail: `EXTERNAL_IMAGE_TEST_REPORT.md`.

### 2.4 A dataset missing required capabilities — ✅

`notes_only.csv` (`note, operator, qty`) produces:

```
⚠ vision  ⚠ production  ⚠ anomaly  ⚠ forensics  ⚠ economics  ⚠ simulation
"6 analysed features unavailable — why?"
  vision: a CSV upload contains no images. …
  production: no numeric process columns (utilisation, queue, temperature, cycle time, …) were detected; found 0
  anomaly: anomaly detection needs at least two numeric process columns to build a PCA space; found 0
  forensics: no station or machine column was detected
  economics: no cost, price or revenue columns were detected. …
  simulation: the what-if engine is calibrated to the supplied Model 3 export; an uploaded CSV is profiled but not simulated.
```

No crash, no `0 0 0`, no invented feature. Through the API the same dataset returns
`status: unavailable` with a reason from `/api/production/anomalies?key=user:notes_only`
and from `/api/datasets/{key}/quality` (404 with an explanation, because per-column
profiles exist for the supplied archives only).

---

## 3. What is deliberately *not* claimed

### 3.1 Uploaded datasets are profiled, not analysed

`UPLOAD → DATASET ID → PROFILER → CAPABILITY MAP → CATALOG` is implemented, and the
capability map drives what the Datasets page shows. The analysis pages (Production,
Problem Flow, What-If, Economics) remain calibrated to the supplied Model 3 export: the
re-simulation engine, the bottleneck weights and the calibration identity all depend on
that schema, and `available.simulation` is `false` for uploads for exactly that reason.
Making those pages run on an arbitrary schema is a modelling project, not a fix, and
pretending otherwise would produce the invented numbers this audit exists to prevent.

### 3.2 The propagation graph is wide

The documented route has twelve stations, so at 32 nodes the whole picture only fits at a
low zoom; the graph pans, zooms and re-fits, and the page says so. Labels are legible at
the fitted zoom on a desktop viewport, but a narrow window needs panning.

### 3.3 Honest limitations carried from the data itself

* **No localization.** Class folders only — no boxes, no masks. `VisionMetrics.localization`
  states this and the attention map is labelled as attention, never a defect position.
* **No timestamps.** `Time_Now` is constant 24 across all 605,620 Model 3 rows and Models
  1–2 export no time column, so rows are independent replications; every ordered view is
  the *documented route order*, not an observed sequence, and says so.
* **No join key.** The image archive and the simulation exports share no identifier, so a
  defect class cannot be attached to a station from data. Such edges exist only as
  engineer-declared `assumed` edges.
* **No costs.** No export contains a price, cost, scrap, rework or downtime value;
  economics stays behind a rate card.
* **Feedback does not retrain.** Decisions are stored with the evidence payload shown and
  are never consumed by the model pipeline; the API and UI both say this.
* **Cell 4 capacity contradiction.** Exported utilisation implies ~5 parallel resources
  where the specification documents 4. Reported as a finding, shown in the validation
  residuals, never tuned away.

### 3.4 Test-suite blind spots

No browser automation in CI (the walkthrough is manual, the API smoke test is automated);
no frontend unit tests (the client is covered by `tsc`, the build and the walkthrough);
timing-sensitive numbers depend on the fixed `SIM_RANDOM_SEED`, so tests assert direction
and consistency rather than exact magnitudes.

---

## 4. What this audit changed

Two passes. The first (`RECOVERY_BASELINE.md` §2, 17 items) restored the application after
the architecture refactor had replaced the catalog, analytics, forensics, simulation and
vision services with stubs that returned fabricated results. The second (§6, 15 further
items) was driven by actually walking the app in a browser against the rebuilt services and
found, among others: a 500 from `sort_values(reverse=…)`; numpy scalars breaking JSON; an
anomaly report that took 68 s (now 3.5 s); a UI whose every design token was undefined; a
landing page that crashed to a blank screen; a money card that could never appear; a
What-If comparison that reported a 12 % loss for a capacity *increase*; and a propagation
graph that had lost the layout for 17 of its 32 nodes.

Nothing in the delivered application returns a fabricated result. Where a capability is
unsupported it answers `unavailable` with a reason, and where a number is a model output
rather than a measurement it is labelled as one.
