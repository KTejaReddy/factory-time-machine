# FINAL_REPORT_FORMAT.md

**What the downloadable analysis report contains, in which format, and how it stays traceable
to one dataset.**

Companion documents: `MULTI_DATASET_ARCHITECTURE.md` (case files and isolation),
`REPAIR_ANALYSIS.md` (how the repair section is computed), `API_REFERENCE.md` (the endpoint).

---

## 1. Endpoint and formats

```http
GET /api/workspace/report?key=<catalog key>&format=md|csv|json&download=true|false&refresh=false
```

| Parameter | Meaning |
|---|---|
| `key` | the dataset's catalog key — required; an unknown key returns 404, a known dataset with a missing source file returns 409 |
| `format` | `md` (default), `csv`, `json` |
| `download` | `true` adds `Content-Disposition: attachment` |
| `refresh` | `true` recomputes the analysis instead of using the saved run |

Without `refresh`, the report is built from the **saved** analysis run, so the document a user
downloaded can always be reproduced — reopening a dataset never silently changes the numbers
behind an already-issued report. `analysis_run_id` and `analysis_saved_at` in the header say
which run was used.

File names: `report-<dataset id>-<UTC timestamp>.<ext>`, e.g.
`report-ds-factory-batch-a-20260920-012714.md`.

The three download links on the analysis-complete card and the Reports page are exactly these
URLs — nothing is generated client-side.

---

## 2. Traceability header

Every format carries the identity of one dataset. In Markdown/JSON:

| Field | Example |
|---|---|
| Dataset | Uploaded dataset: factory_batch_a |
| Dataset ID | `ds-factory-batch-a` |
| Catalog key | `user:factory_batch_a` |
| Source file | `factory_batch_a.csv` |
| Uploaded | 2026-09-20T00:01:39.902914 |
| Status | Analysis ready |
| Rows × columns | 720 × 11 |
| Report generated | 2026-09-20T01:27:14.9+00:00 |
| Analysis run | #4 (2026-09-20T01:14:51) |

In CSV, `dataset`, `dataset_id` and `generated_at` are the **first three columns of every
row**, so a filtered or pivoted export still says whose numbers they are.

---

## 3. Markdown layout

```text
# Analysis report - <dataset name>
  (traceability table)

## Available analysis          capability | yes/no | reason when unavailable
## Dataset summary             quality / process / production / economic /
                               recommendation / confidence (score + basis)
## Data quality                rows, columns, missing cells, duplicates, constant columns
## Process anomalies           threshold, anomalous fraction, per-row scores, drivers
## Production constraint       ranking, utilisation, queue, headroom, method
## Forensic finding            leading case, statistic, first divergence, co-occurring, timeline
## Failure propagation         nodes, edges, assumed edges, linkage notice
## Economic impact             lines with the rate each used, total, currency
## Repair options              candidate table + cheapest supported effective repair
## What-if scenarios           saved runs with completed parts and delta
## AI finding                  source/provider/model, finding, evidence lines, recommendation
## Human feedback              engineer decisions with notes
## Limitations                 every unsupported step, with its reason
  (advisory footnote)
```

Anything unavailable is printed as a dash with the reason, never as a zero: `_fmt(None)` is
`-`, and sections that need cost data say so instead of showing 0.00.

---

## 4. JSON payload

Top-level keys:

```json
{
  "dataset":   { "id", "dataset_id", "key", "name", "uploaded_at", "status", "rows", "columns", "source_file" },
  "generated_at": "...", "analysis_saved_at": "...", "analysis_run_id": 4,
  "capabilities": { "available": {...}, "reasons": {...} },
  "sections": { "quality", "process", "production", "anomaly", "forensics",
                "propagation", "economics", "repairs" },
  "summary": { "dataset", "quality", "process", "production", "economic",
               "recommendation", "confidence", "limitations" },
  "ai": { "available", "source", "provider", "model", "generated_at", "finding", "evidence" },
  "rate_card": { "rates", "engineer", "updated_at" } | null,
  "scenarios": [ { "id", "name", "created_at", "engine", "summary", "scenario" } ],
  "feedback": [ { "finding_title", "decision", "engineer", "note" } ],
  "limitations": [ "..." ],
  "advisory": "Advisory decision support only. ..."
}
```

`sections` is the same payload the workspace pages render, so the JSON is the machine-readable
equivalent of the whole case file for one dataset.

---

## 5. CSV layout

One metric per row — the shape a spreadsheet wants:

```text
dataset,dataset_id,generated_at,section,item,value,source
```

| `section` | Examples of `item` |
|---|---|
| `dataset` | `catalog_key`, `uploaded_at`, `status`, `rows`, `columns` |
| `capability` | `vision`, `production`, `anomaly`, `forensics`, `economics`, `simulation` (source = the reason) |
| `quality` | `missing_cells`, `duplicate_rows`, `constant_column_count` |
| `anomaly` / `production` / `forensics` / `propagation` / `economics` / `process` | every scalar in the section, flattened with dots (`bottleneck.label`, `ranking[0].score`) |
| `summary` | one row per headline verdict + `confidence` |
| `repair` | one row per candidate for expected loss reduction, intervention cost and net impact, plus `cheapest_supported_effective_repair` |
| `scenario` | `<name> - completed parts`, `<name> - delta` |
| `limitation` | one row per limitation |

Timestamps are ISO-8601 UTC. `NULL`-like values are written as `-` so an empty cell always
means "not applicable here" rather than "not measured".

---

## 6. Live excerpt (Dataset A, 2026-09-20)

```markdown
| Dataset | Uploaded dataset: factory_batch_a |
| Dataset ID | `ds-factory-batch-a` |
| Catalog key | `user:factory_batch_a` |
| Report generated | 2026-09-20T01:27:14.906104+00:00 |

## Available analysis
| vision | no | a CSV upload contains no images. ... |
| production | yes | |
| anomaly | yes | |
| forensics | yes | |
| economics | yes | |
| simulation | no | the what-if engine is calibrated to the supplied Model 3 export; ... |

## Dataset summary
- **Quality:** clean
- **Process:** 0.56% of scored rows sit beyond the 3.77 distance threshold; ...
- **Production:** Assembly is the leading constraint (utilisation 0.942, score 1.000)
- **Economic:** 29,424 INR over the documented horizon, from measured quantities and stated rates
- **Recommendation:** Cut downtime by 20% - highest net benefit per unit of intervention cost ...
- **Confidence:** 0.80 (forensic case confidence on this dataset; 4 measured columns diverge ...)
```

Dataset B's report for the same command shows `Economics | no | the available rates do not
match any quantity that exists in this dataset` and, under Repair options,
"Repair cost comparison unavailable until a rate card is provided." — the honest result of a
dataset with no cost columns.

---

## 7. Isolation guarantee

* `build_report()` reads the dataset's own record, saved run, rate card, scenarios and feedback
  — all keyed by `dataset_key`;
* the rendered Markdown/CSV contain only the dataset named in the header (verified: report A
  mentions neither `factory_batch_b` nor Dataset B's findings, and vice versa);
* the AI narrative inside the report was produced from that dataset's evidence bundle only
  (`MULTI_DATASET_ARCHITECTURE.md` §7);
* the report's `analysis_run_id` points at the run it was built from, so two reports of the
  same dataset can be compared honestly.
