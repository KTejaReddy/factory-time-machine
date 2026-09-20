# DATASET_GUIDE.md

**How to prepare your own CSV so Factory Time Machine can analyse it — and what to do when it
says a feature is unavailable.**

This guide is about *your* file. For the supplied archives see `DATASET_EXPLANATION.md` and
`DATASET_SCHEMA.md`; for the case-file machinery see `MULTI_DATASET_ARCHITECTURE.md`.

---

## 1. The short version

1. Export one row per **observation** (a shift, a batch, a run, a part) with **one column per
   station or machine** — or one row per *station-observation* with a station column.
2. Name the columns the way a plant names them. Detection is deliberately name-based: the
   profiler reads column names and dtypes and never guesses a schema, because guessing is how
   fabricated results start.
3. Upload it (Dashboard → **Upload Dataset**, `.csv`, ≤ 25 MB). The app profiles it, builds the
   capability map, creates a **new case file** and switches to it.
4. Press **Run analysis**. Everything the capability map promised is computed and stored against
   that dataset.
5. Add a rate card on `/economics` if you want money figures.

---

## 2. What the profiler looks for

Lower-case substring matches against the column name:

| Concept | Name fragments that are recognised | What it unlocks |
|---|---|---|
| **Stations** | `station`, `machine`, `cell`, `press`, `workcenter`, `work_center`, `line_id`, `line_name` | per-station production analysis, forensic cases per station, the measured propagation view, the bottleneck ranking |
| **Process variables** | `util`, `queue`, `temp`, `cycle_time`, `cycle time`, `pressure`, `speed`, `rpm`, `flow`, `vibration`, `oee`, `defect_rate`, `scrap_rate`, `downtime`, `down_time`, `throughput`, `output`, `wip`, `capacity` | anomaly detection (needs ≥ 2), the process summary, repair candidates |
| **Cost / value** | `cost`, `price`, `revenue`, `spend`, `expense`, `rate_usd`, `ratecard`, `rate_card` | economics and priced repairs from the data itself |
| **Batch identifiers** | `batch`, `lot`, `run`, `order`, `job`, `serial`, `part_id` (+ names ending `id`, `_id`, `code`, `key`, `name`) | grouping, per-batch context |
| **Timestamps** | `timestamp`, `datetime`, `date`, `time_now`, `start_time`, `end_time`, `created_at` | typed as time columns (ordering is never claimed unless the values actually vary) |

Numeric columns are what the analysers act on; text columns are treated as categories or
identifiers.

### Units come from the name

* **Durations** — `_h`/`hour` → hours, `_min`/`minute` → minutes (÷60), `_s`/`sec` → seconds
  (÷3600). A duration whose unit the name does not state is reported *with the reason* instead
  of being assumed.
* **Percentages** — `util_pct`, `percent`, `%` → divided by 100. A utilisation column whose mean
  is above 1 is also read as a percentage, and the assumption is returned with the number
  (`utilization_note`), never applied silently.
* **Currency** — from a `currency`-like column (INR/USD/EUR/GBP/₹/$/€/£) or from the rate card;
  otherwise reported as "currency not stated".

---

## 3. Two shapes that work

### 3a. Long format — one row per station-observation (recommended)

```csv
batch_id,station,cycle_time_s,station_util,queue_len,downtime_hours,scrap_count,rework_count,scrap_cost,downtime_cost,unit_value
B0001,Cutting,41.32,0.7132,5.21,0.512,3,2,118.40,339.80,96.10
B0001,Welding,52.08,0.8547,17.93,1.031,5,4,117.90,341.20,96.05
```

→ stations: `station`; process: `cycle_time_s`, `station_util`, `queue_len`, `downtime_hours`;
costs: `scrap_count`, `scrap_cost`, `downtime_cost`, `unit_value`; batch: `batch_id`.
This is the demo's Dataset A: `✓production ✓anomaly ✓forensics ✓economics`, plus a repair
ranking as soon as an intervention cost is supplied.

### 3b. Wide format — one column per station

```csv
shift_id,Cutting_cycle_s,Welding_cycle_s,Assembly_cycle_s,Cutting_queue,Welding_queue,Assembly_queue,scrap_cost
S0001,41.2,52.1,64.0,5,18,31,118.4
```

→ station names appear in the column names (matched by the station hints); each `*_queue` /
`*_cycle` column becomes a process variable. Anomaly detection and economics work; per-station
grouping is weaker because each station is a separate column rather than a shared one.

### 3c. A dataset with only images

Unavailable by design: an upload is a CSV. Images go through `/inspect` — the archive, or
**Test with a new image** for images that are explicitly *not* training data. A CSV-only case file
reports `vision: no` with that reason.

---

## 4. What each capability needs

| Capability | Needs | If missing |
|---|---|---|
| **production** | ≥ 1 station-like column and numeric process columns | "no numeric process columns … were detected; found 0" |
| **anomaly** | ≥ 2 numeric process columns (PCA + Mahalanobis space) | "anomaly detection needs at least two numeric process columns to build a PCA space" |
| **forensics** | a station/machine column (group contrasts) | "no station or machine column was detected" |
| **economics** | a cost/price/revenue column, or a stored rate card | "no cost, price or revenue columns were detected…" / "the available rates do not match any quantity that exists in this dataset" |
| **vision** | images — never a CSV | "a CSV upload contains no images…" |
| **simulation** | the documented Model 3 route (supplied archives) | "the what-if engine is calibrated to the supplied Model 3 export; an uploaded CSV is profiled but not simulated" |

All six reasons are shown in the UI (`⚠ <feature> — why?`) and returned by
`/api/workspace/datasets/{key}` in `capabilities.reasons`.

---

## 5. Adding costs when your file has none

If economics is off, **that is the honest answer, not a bug**: without a rate this app will not
publish a money figure. Open `/economics` (or the Repairs page) and store a rate card for this
dataset:

| Field | Meaning |
|---|---|
| `currency` | INR, USD, EUR, GBP … |
| `cost_per_unit_scrapped` | cost of one scrapped unit |
| `rework_cost_per_unit` | cost of reworking one unit |
| `downtime_cost_per_hour` | cost of one hour of downtime |
| `holding_cost_per_unit_hour` | cost of holding one part for one hour |
| `margin_per_unit` | value of a good unit |
| `intervention_cost_fixed` | one-off cost of the intervention you are evaluating |
| `intervention_cost_per_capacity_unit` | cost per unit of added capacity |

The card is stored **against that dataset only**. Storing one for Dataset A leaves Dataset B at
"rate card required"; the rate card always overrides a dataset column for the dataset it belongs
to. With `intervention_cost_fixed` supplied, the repair engine can finally rank candidates and
name the cheapest supported effective repair (`REPAIR_ANALYSIS.md` §4).

---

## 6. Data quality it will report (and never silently fix)

* missing cells and duplicate rows, counted on your file;
* columns with a single constant value;
* which of your columns it typed as categorical / numerical / identifier / timestamp;
* minimum, mean and maximum of each numeric column.

Nothing is imputed, dropped or repaired. If a column is unusable the analysis says so.

---

## 7. Checklist before you upload

- [ ] one row per observation, consistent units within a column;
- [ ] a station/machine column (or station names inside the column names);
- [ ] at least two numeric process columns;
- [ ] a cost, price or revenue column **or** a rate card you intend to store;
- [ ] no more than 25 MB, `.csv`, UTF-8;
- [ ] a filename that says what the dataset is — it becomes the case-file name
      (`factory_batch_a.csv` → `ds-factory-batch-a`).

Uploading a second file is safe: it creates a second case file, and the first keeps its analysis,
rate card, scenarios, AI findings, feedback and reports.

---

## 8. Verified examples

| File | Shape | Detected | Result |
|---|---|---|---|
| `factory_batch_a.csv` (demo) | long, 720 × 11 | 1 station field, 5 process columns, 4 cost/value columns, batch ids | economics computed from its own columns (29,424 INR); repair "Cut downtime by 20 %" at ≈ 8.4× benefit-cost |
| `factory_batch_b.csv` (demo) | long, 720 × 9 | 1 station field, 4 process columns, 1 cost column | production/anomaly/forensics on; economics off with the reason; bottleneck Press 2 at 90.9 % utilisation |
| notes-only CSV (`note, operator, qty`) | 3 columns, no manufacturing names | 0 stations, 0 process columns | all six capabilities off, each with its own reason; no crash, no zeros |
