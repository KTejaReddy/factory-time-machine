# MULTI_DATASET_ARCHITECTURE.md

**How Factory Time Machine keeps one case file per dataset — and never mixes them.**

This document describes the layer that turns the analyser into a multi-dataset platform:
what a case file *is*, where it is stored, which rule keeps Dataset A's numbers out of
Dataset B's pages, and how the whole thing was verified.

Read it together with:

| Document | Answers |
|---|---|
| `SYSTEM_EXPLANATION.md` | what the analysis itself does, in plain language |
| `DATASET_SCHEMA.md` | what the supplied archives contain |
| `DATASET_GUIDE.md` | how to prepare your own CSV |
| `REPAIR_ANALYSIS.md` | how repair options and costs are derived |
| `FINAL_REPORT_FORMAT.md` | what the downloadable report contains |
| `API_REFERENCE.md` | endpoint reference (including `/api/workspace/*`) |

---

## 1. The product rule

```text
USER UPLOADS DATASET
        ↓
DATASET PROFILE + CAPABILITY DETECTION
        ↓
NEW DATASET WORKSPACE  (/dataset/<dataset-id>)
        ↓
RUN ANALYSIS  → title: stored against this dataset
        ↓
AI FINDING → REPAIRS → WHAT-IF → REPORT → DOWNLOAD
```

Uploading Dataset B **does not replace** Dataset A. A second upload creates a second case
file; both remain openable, each with its own analysis, rate card, scenarios, AI findings
and engineer feedback.

The rule is enforced in one place — a catalog key — and every dataset-scoped table carries
it. Nothing is inferred from "the current page".

---

## 2. Identity

| Field | Example | How it is derived |
|---|---|---|
| Catalog key | `user:factory_batch_a` | `user:<sanitised file stem>` for an upload; `model1`…`model3`, `images` for the supplied data |
| Dataset id | `ds-factory-batch-a` | `slugify(key)` → stable, URL-safe, used in `/dataset/:id` |
| Name | `Uploaded dataset: factory_batch_a` | display label |
| Upload date | file mtime (UTC) | the file's own date, kept stable once recorded |
| Source file | `factory_batch_a.csv` | basename inside `data/user_datasets/` |
| Status | `Analysis ready` | derived: `analysis_ready` / `partial_analysis` / `images_only` / `file_missing` / `pending_build` |

`ds-<slug>` is the same on every request, so a saved URL, a downloaded report and a bookmark
all keep pointing at the same case file.

---

## 3. Storage

SQLite (SQLAlchemy models, PostgreSQL-compatible types) at
`data/cache/factory_time_machine.sqlite3`. Tables that hold dataset-specific results carry
`dataset_key`:

| Table | Holds | Scoped by |
|---|---|---|
| `dataset_records` | the case file itself: id, name, kind, source file, upload date, status, rows/columns, profile, capability map, `present` | `key` (unique) |
| `analysis_runs` | each stored analysis result set (dataset info, capabilities, sections, summary, AI finding) with its computation time | `dataset_key` |
| `rate_cards` | the engineer's cost rates for that dataset | `dataset_key` (unique) |
| `scenario_runs` | what-if runs: scenario definition, baseline, result, validation | `dataset_key` |
| `feedback_items` | the engineer's confirm/reject verdicts, with the exact evidence they saw | `dataset_key` |
| `narratives` | cached AI narratives keyed `topic:subject` | subject id contains the key |
| `ai_call_logs` | every AI attempt (provider, model, latency, error) | global, by design |
| `app_preferences` | the active dataset, so a restart lands back in the same case file | key/value |

Two schema notes:

* `create_all` creates missing tables but never alters existing ones, so
  `_ADDED_COLUMNS` in `backend/app/db.py` applies idempotent `ALTER TABLE … ADD COLUMN`
  migrations for `dataset_key` on databases created before this layer existed.
* Losing a source file never deletes history: the record stays with `present = false` and
  status `Source file missing`, and the API answers 409 with an explanation instead of a 500.

---

## 4. What "active dataset" means

Three places agree, in this order:

1. `localStorage.activeDataset` (what the user clicked last);
2. the server-side `app_preferences` value (survives closing the browser);
3. a default that prefers the **newest uploaded** dataset, then any present dataset —
   never `model3`.

`_default_key()` in `backend/app/routers/workspace.py` implements the order. Switching the
dataset in the header writes all three and calls `notifyWorkspaceChanged()`, so every page
re-fetches against the new key rather than only re-rendering its title.

---

## 5. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workspace/datasets` | the history: every case file, newest first, with saved-artifact counts |
| `GET` | `/api/workspace/datasets/{key-or-id}` | one case file's header + saved history + rate card + scenarios |
| `DELETE` | `/api/workspace/datasets/{key-or-id}` | remove an uploaded CSV (history is retained) |
| `GET`/`POST` | `/api/workspace/active` | read / set the active dataset |
| `POST` | `/api/workspace/analysis/run?key=` | compute **and store** the full result set for one dataset |
| `GET` | `/api/workspace/analysis?key=` | the saved result set (or `not_run`) |
| `GET`/`POST`/`DELETE` | `/api/workspace/rate-card?key=` | the dataset's cost rate card |
| `GET`/`POST` | `/api/workspace/repairs?key=` | repair options + cheapest supported effective repair |
| `GET` | `/api/workspace/summary?key=` | the "analysis complete" card: main issue, repair, impact, confidence, limitations, headline cards, download links |
| `GET` | `/api/workspace/report?key=&format=json\|md\|csv&download=` | the final report |
| `POST` | `/api/upload/` | upload a CSV → profile → capability map → catalog rebuild → new case file |

Every dataset-scoped endpoint takes an explicit `key`. A key that is not loaded raises 404
(or 409 when the case file is known but its source file is gone) — the router never guesses,
so a page cannot silently render another dataset's numbers.

---

## 6. Pages

| Route | Case section |
|---|---|
| `/dataset/:id` | workspace home: identity, status, capability map, analysis-complete card, download links, section index, saved history |
| `/` | Overview — what was found |
| `/inspect` | Inspect — defect analysis and image inspection |
| `/investigate` | Investigate — forensic findings for the active dataset |
| `/flow` | Problem flow — propagation graph |
| `/production` | Where the production constraint is |
| `/economics` | Rate card and cost breakdown |
| `/repairs` | Repair options, cheapest supported effective repair |
| `/what-if` | Simulated changes, per-dataset scenario history |
| `/ai` | Ask AI, grounded on the active dataset's evidence |
| `/review` | Engineer confirm/reject, filtered by dataset |
| `/reports` | Download the final report (MD/CSV/JSON) |
| `/datasets` | Dataset history, schema, quality and capability map |

---

## 7. Isolation rules

1. **One evidence bundle per dataset.** `_overview_evidence(key)` in
   `backend/app/routers/ops.py` is the only thing the language model is given. Every field
   in it is derived from *this* dataset's frame, profile, per-dataset issues and capability
   map. Per-dataset issues are those whose message starts with the dataset key, so
   `model1`'s missing cells can never appear in an upload's narrative.
2. **No default model.** Asking the AI requires a dataset key (`400` when absent); the key
   is embedded in the narrative cache subject, so the same question on two datasets cannot
   return the other's answer.
3. **Repairs are discovered, not templated.** Candidates come from the columns the profiler
   detected in *this* dataset (plus the documented-route re-simulation when it applies).
4. **Rate cards are per dataset.** A rate card is never shared implicitly; storing one for
   Dataset A leaves Dataset B at "rate card required", with the reason shown.
5. **What-if history is per dataset.** The stored scenario list is filtered by `dataset_key`.
6. **Reports name their dataset.** Every report carries dataset name, dataset id, catalog key,
   source file, upload date and generation time, plus the id of the analysis run it used.
7. **Saved results are reused.** Reopening a dataset shows its stored analysis instead of
   recomputing — pressing *Re-run analysis* is an explicit action that writes a new run.

---

## 8. Presence flags reconcile on read

A case file's `present` flag is repaired whenever the history is read
(`_refresh_presence()` → `workspace.reconcile_presence()`), because it can drift:

* a catalog build that ran before an upload was in place used to leave the dataset flagged
  `file_missing` for the life of the database — the switcher then told the user their dataset
  was gone even though the CSV was on disk;
* being absent from one build's summaries means "not loaded", which is not the same as
  "deleted". A CSV that is on disk but not yet loaded is labelled
  `Waiting for catalog rebuild`.

The file on disk is the authority for an upload. Reconciliation touches only the presence
flag and its label; it never recomputes an analysis.

---

## 9. Verification

Automated (`python -m pytest tests -q`, 125 passed):

* `tests/test_multi_dataset.py` — one case file per dataset; two uploads keep separate
  analysis, rate cards, scenarios, feedback and reports; dataset A's bottleneck is Assembly
  and dataset B's is Press 2; reports contain only their own dataset; deleting a source keeps
  the history; AI evidence is scoped to one dataset; a wrongly flagged dataset recovers.
* `tests/test_upload_flow.py` — upload, sanitisation, capability map, catalog rebuild.

Browser walkthrough (recorded in `FINAL_AUDIT.md` §4): upload A → analyse → upload B → analyse
→ switch back to A (history intact) → reload (both datasets still there) → identical AI
question on A and B (different, dataset-correct answers) → report A and report B (no
cross-references).

The suite itself is part of this guarantee: an autouse fixture in `tests/conftest.py` restores
the exact state of every pre-existing case-file row and deletes everything a test created, so
running the tests cannot leave fake datasets in the developer's history.
