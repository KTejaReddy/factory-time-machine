# FINAL_AUDIT.md

**End-to-end audit of Factory Time Machine, performed against the running application on
2026-09-20.** Everything below was reproduced live — in the API and in the browser, driving the
real upload path — not read off the source. Where a feature cannot be supported by a dataset it
is marked as such and the reason is stated.

Companion documents: `MULTI_DATASET_ARCHITECTURE.md`, `REPAIR_ANALYSIS.md`,
`FINAL_REPORT_FORMAT.md`, `RECOVERY_BASELINE.md` (the earlier restoration campaign),
`TEST_REPORT.md`, `SECURITY_AUDIT.md`, `EXTERNAL_IMAGE_TEST_REPORT.md`,
`SYSTEM_EXPLANATION.md`, `ALGORITHM_REFERENCE.md`, `FUNCTION_REFERENCE.md`,
`API_REFERENCE.md`, `DATASET_GUIDE.md`, `DATASET_SCHEMA.md`.

Verification commands:

```bash
python -m uvicorn app.main:app --port 8001     # from backend/
cd frontend && npm run dev                     # Vite on 5173, /api proxied to 8001
python -m pytest tests -q                      # 125 passed (≈178 s)
cd frontend && npm run typecheck && npm run build   # clean / built
```

---

## 1. Required status table

| Area | Status | Evidence |
|---|---|---|
| Dataset Upload | ✅ | uploaded `factory_batch_b.csv` through the browser's own file input; `POST /api/upload/` sanitises the name, caps at 25 MB, parses, rebuilds the catalog and returns the profile + capability map |
| Dataset Persistence | ✅ | SQLite (`data/cache/factory_time_machine.sqlite3`) with `dataset_records`, `analysis_runs`, `rate_cards`, `scenario_runs`, `feedback_items`, `app_preferences`; survives a browser reload and a backend restart |
| Dataset Switching | ✅ | header selector writes `localStorage` + server preference; every page re-fetches against the new key (verified: A → B → A) |
| Dataset Isolation | ✅ | A's bottleneck is Assembly, B's is Press 2; A shows 3 saved analyses and its rate card after switching back, B shows 1 and none; no B token on A's pages beyond the switcher list |
| Dataset Profiling | ✅ | B profiled as 720 × 9 with 1 station field, 4 process columns, 1 cost column; `util_pct` recognised as a percentage |
| Capability Detection | ✅ | per dataset: A `✓production ✓anomaly ✓forensics ✓economics ⚠vision ⚠simulation`; B the same with economics **unavailable and explained**; `images` archive vision-only; a notes-only CSV returns all six off with reasons |
| Vision | ✅ | crack specimen → crack 98.3 %, flip-agreement 100 %, attention map; OOD guard flags non-factory images (archive samples ≤ 1.40 vs threshold 1.6) |
| External Image Testing | ✅ | `data/external_test/`, `training_data: false` in every response; see `EXTERNAL_IMAGE_TEST_REPORT.md` |
| Anomaly Detection | ✅ | PCA + Mahalanobis on the dataset's own process columns; 0.56 % of scored rows beyond the threshold for both demo uploads; a 605,620-row report in 3.5 s (was 68 s) |
| Forensics | ✅ | per-dataset cases (`station:Assembly` for A), statistics from that dataset's own columns; the "lowest-output" case is not offered where the dataset has no output series |
| Failure Propagation | ✅ | 32 nodes / 26 edges / 0 dangling for model3; uploads get a measured station graph and "No propagation path can be established from the available data" where no route exists; no 500s (`route[0]` guarded) |
| Bottleneck | ✅ | utilisation resolved as a fraction from the column name and magnitude (`util_pct` 90.9 %, not 9094.7 %) |
| Economics | ✅ | A: 29,424 INR from its own `scrap_cost`/`downtime_cost`/`unit_value` columns; B: unavailable with the reason "the available rates do not match any quantity that exists in this dataset"; nothing invented |
| Repair Analysis | ✅ | candidates discovered from the dataset's columns (A: 5, B: 4) with measured quantity, effect, cost, net impact, confidence and assumptions |
| Cheapest Supported Repair | ✅ | A: "Cut downtime by 20 %", net ≈ 1,260,494 INR at ≈ 8.4× benefit-cost ratio, selected as highest net benefit per unit intervention cost; B: `cost_comparison_unavailable` → "Repair cost comparison unavailable until a rate card is provided." |
| What-If | ✅ | dataset-scoped scenario parameters; documented-route re-simulation for the supplied archives; uploads are refused with the reason rather than fed old demo values |
| AI Recommendation | ✅ | live LLM (gpt-oss-120b) grounded on backend evidence; deterministic template fallback with no key; every number originates in Python |
| AI Dataset Isolation | ✅ | identical question on A and B → A answers "Assembly at 94.2 % utilisation, 6.16 % headroom", B answers "Press 2 at 90.95 %, queue 48.61, headroom 9.95 %"; neither mentions the other's dataset or numbers |
| Human Feedback | ✅ | decisions stored per dataset with the evidence shown; Review page and `/api/review/summary?key=` filter by dataset; never retrains anything (stated in the UI and API) |
| Final Report | ✅ | `GET /api/workspace/report?key=&format=md\|csv\|json`; 8,281 / 48,280 / 38,239 bytes for dataset A; traceability header on every format |
| Downloads | ✅ | MD, CSV and JSON links on the analysis-complete card, the Reports page and the workspace; all served as attachments with dataset-scoped file names |
| Security | ✅ | path-traversal filename lands as a bare name inside the upload dir; wrong type rejected; size capped; per-dataset key required everywhere; see `SECURITY_AUDIT.md` |
| Automated Tests | ✅ | 125 passed (`python -m pytest tests -q`), including 18 multi-dataset/isolation tests and the report/CSV traceability contracts. The suite restores the developer's workspace, so a run leaves the registry byte-identical |
| Browser Testing | ✅ | full 16-step acceptance run below, driven through the UI |

Legend: ✅ tested and working · ⚠ partial or capability-dependent, disclosed · ❌ not possible
with this data.

---

## 2. Defects found in this session, and their fixes

Each was reproduced in the running app first, then fixed, then re-verified.

### 2.1 Case files came back from the dead (and A/B were flagged "source file missing")

* **Problem** — the dataset history listed `user:temporary_probe` and
  `user:machine_shift_export` as "Source file missing" although they were only ever test
  fixtures, and after a full test run `factory_batch_a`/`factory_batch_b` were themselves
  flagged missing while their CSVs sat in `data/user_datasets/`.
* **Cause** — two things. `sync_registry` flipped `present=False` whenever a key was absent
  from *that build's* summaries, which also happens when a build simply did not load a file
  (or ran against a patched upload directory in a test). And the upload tests drove the real
  endpoint, so they wrote case-file rows into the developer's database and then mutated the
  real rows again by building against an empty temporary directory.
* **Fix** — `workspace.reconcile_presence(catalog)` reconciles the flag against the file on
  disk on every history read (`_refresh_presence()` in the workspace router); a file that
  exists but is not loaded is labelled `Waiting for catalog rebuild`, never "missing"; the
  flip in `sync_registry` now requires the file to be genuinely gone; and an autouse fixture
  in `tests/conftest.py` snapshots and restores every pre-existing row and deletes whatever a
  test created.
* **Verification** — a deliberately corrupted row recovered on the next page load
  (`test_a_wrongly_flagged_dataset_recovers_without_a_restart`); a full suite run now leaves
  the six case files untouched; the two fixture rows were removed and do not return.

### 2.2 An uploaded dataset's AI narrative quoted the supplied archives

* **Problem** — dataset A's report contained
  "… 36,000 missing cells (model1), 37 missing cells (model3), … constant Time_Now = 24 in
  model3 rows" — findings the user's own CSV never produced.
* **Cause** — the AI evidence bundle (`_overview_evidence`) was assembled from global
  catalog state: every dataset's quality issues, the whole models list, the image/simulation
  linkage, and three hardcoded limitations that were false for an upload with cost columns.
* **Fix** — the bundle is now scoped to one dataset: issues filtered by the key prefix,
  `models` limited to the documented archives (and never naming an upload as part of the
  facility), linkage only for a supplied archive, per-dataset missing/duplicate counts,
  limitations taken from that dataset's capability map, inspection context only where the
  dataset supports vision. The reworded supplied-archive limitations say "the supplied
  archives" instead of "no dataset".
* **Verification** — `test_ai_evidence_never_quotes_another_dataset`; dataset A's evidence
  contains no `model1`/`model2`/`model3`/`Time_Now` token, and the regenerated report contains
  none either (0 lines, was 1). Model1's evidence no longer carries model3's row counts.

### 2.3 A supported feature was labelled "not supported by this dataset"

* **Problem** — the Overview's Process card always said "not supported by this dataset", and
  the Economics card always said "rate card required", even for a dataset whose anomaly and
  economic analyses had run.
* **Cause** — the cards read `summary.sections…`, a field the summary endpoint never returned,
  so the check was always falsy.
* **Fix** — `/api/workspace/summary` now returns a compact `highlights` block (quality counts,
  process availability + anomalous fraction + reason, production bottleneck, economics
  availability + reason) derived from the saved run, and the Dashboard renders it with the
  recorded reasons; the production figure is shown as a percentage of the resolved fraction.
* **Verification** — A shows "720 rows · 0 missing cells · 0 duplicates", "0.56 % of scored rows
  beyond the threshold", "Assembly (utilisation 94.2 %)", "computed"; B shows the economics
  reason instead of a fake zero.

### 2.4 Utilisation was reported 100× too large

* **Problem** — dataset B's Production page said Press 2 was "9094.7 %" busy.
* **Cause** — a plant export writes `util_pct` as 91.0; the bottleneck reader took the column
  mean verbatim as a fraction.
* **Fix** — `bottleneck.utilisation_fraction()` resolves the unit from the column name and the
  magnitude, returns the assumption with the number (`utilization_note`), and the raw mean is
  kept as `utilization_raw_mean` so nothing is hidden.
* **Verification** — Press 2 now reads 0.9095 (90.9 %), Press 1 0.5506, Press 3 0.4791, each
  carrying its note; dataset A's fraction-valued `station_util` is unaffected.

### 2.5 The propagation endpoint could 500 on an empty route

* **Problem** — `GET /api/diagnostics/propagation` raised `IndexError: list index out of
  range` at `route[0]` (three times in the backend error log).
* **Cause** — the documented-route builder indexed the first station without checking that the
  route was non-empty.
* **Fix** — an empty route returns an empty graph with "No propagation path can be established
  from the available data." instead of an exception.
* **Verification** — all five keys return 200 with sensible node/edge counts; the error log
  stays clean.

### 2.6 The test suite polluted the workspace

Covered by 2.1 — recorded here because the symptom (fake datasets in the history, healthy
datasets flagged missing) looked exactly like a product bug.

---

## 3. Browser acceptance run (16 steps)

Performed in the running app on 2026-09-20. Dataset A (`factory_batch_a.csv`, 720 × 11) was
already present from the previous campaign; Dataset B (`factory_batch_b.csv`, 720 × 9) was
uploaded during this run.

| Step | Result |
|---|---|
| 1. Open the application | ✅ landing page renders; active dataset A; no console errors |
| 2. Upload Dataset B | ✅ file selected in the page's own CSV input, "Upload Dataset" |
| 3. B gets its own workspace | ✅ "DATASET READY — Uploaded dataset: factory_batch_b / ds-factory-batch-b / 720 rows × 9 columns · 1 station field, 4 process columns, 1 cost column" + capability chips + "Start analysis" |
| 4. Run the analysis | ✅ stored as run #3 in 3.4 s; analysis-complete card: main issue `station:Press 2`, repair "No supported repair yet", impact reason, confidence 80 %, download links |
| 5. Overview / Inspect / Investigate / Problem flow / Production / Economics / Repairs / What-If / AI / Review / Reports | ✅ each renders for B; What-If is offered as *unavailable* with its reason; Economics offers the rate-card form |
| 6. Generate the final report | ✅ report built from B's saved run |
| 7. Download it | ✅ MD 8 KB / CSV 48 KB / JSON 38 KB for A; B downloads with its own id in the file name |
| 8. Upload Dataset B → 9. its workspace | ✅ (steps 2–3) |
| 10. Analyse B | ✅ |
| 11. B shows no A results | ✅ B's pages contain no `factory_batch_a`, `Assembly` or A's figures; A carries 3 analyses, B carries 1 |
| 12. Switch back to A | ✅ A's workspace shows its 3 saved analyses, its stored rate card and its own findings (Assembly 94.2 %, economics computed, "Cut downtime by 20 %") |
| 13. A's history preserved | ✅ "Saved history 2 analysis runs" at that point, rate card stored for this dataset, reports regenerated from A's own run |
| 14. Reload the browser | ✅ both datasets still listed; active dataset still A; A's analysis-complete card still populated from the store |
| 15. Both datasets still exist | ✅ 6 case files: A (3 analyses, rate card), B (1 analysis), model1/2/3, image archive |
| 16. Identical AI question on A and B | ✅ different, dataset-correct answers (see the status table row "AI Dataset Isolation") |

Console: no application errors on any route. Backend error log: no unhandled exceptions.

---

## 4. What is deliberately *not* claimed

* **What-If does not run on an arbitrary schema.** The discrete-event engine, its bottleneck
  weights and its calibration identity depend on the documented export. An upload is therefore
  told `simulation: no` with that reason; what-if history is still stored per dataset for the
  datasets it does cover (35 runs for model3).
* **No localization.** Class folders only — no boxes, no masks; the attention map is labelled
  attention, never a defect position.
* **No timestamps in the supplied archives.** Every ordered view is the documented route order,
  not an observed sequence, and says so.
* **No join key between images and the simulation exports.** Defect-class → station edges exist
  only as engineer-declared `assumed` edges.
* **A repair is only priced when both sides are supported.** Intervention cost never exists in
  a dataset; without a rate card the engine publishes one sentence instead of a ranking.
* **Feedback does not retrain anything.**
* **The propagation graph is wide** for model3 (32 nodes) and needs panning on a narrow window.
* **No browser automation in CI**: the walkthrough above is manual, the API smoke test is
  automated, and the frontend is covered by `tsc`, the production build and the walkthrough
  rather than by unit tests.

---

## 5. Overall

The application is a multi-dataset platform: every dataset — supplied or uploaded — gets one
case file with its own identity, profile, capability map, saved analysis, rate card, repair
ranking, scenario history, AI finding, engineer feedback and downloadable report. Dataset A and
Dataset B coexist with different bottlenecks, different economics availability and different AI
answers, and switching between them changes the underlying requests, not just the title.

Nothing in the delivered application returns a fabricated result. Where a capability is
unsupported it answers `unavailable` with a reason; where a number is a model output rather
than a measurement it is labelled as one; and where a required input is missing (intervention
cost, rate card, images, route order) the app asks for it instead of guessing.
