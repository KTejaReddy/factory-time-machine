# DATASET_SCHEMA.md

Everything in this document was **measured directly** from the files shipped in this
folder. Nothing is inferred beyond what the files state, and no column, class, label or
economic value has been invented. Reproduce it with:

```bash
python scripts/inspect_datasets.py --json      # writes data/cache/dataset_report.json
```

Two datasets are provided. They are **not joinable** (see §4), and the application is
built to keep that fact visible instead of hiding it.

---

## 1. Visual dataset — `train.zip` (300.9 MB)

| Property | Value |
|---|---|
| Archive entries | 12,006 (12,000 `.png` + 6 directory entries) |
| Images | **12,000**, PNG, **256 × 256** |
| Channels | Stored RGB but **R = G = B** on every sampled image → effectively grayscale |
| Classes (folders) | `crack`, `hole`, `normal`, `rust`, `scratch` |
| Class balance | **2,400 images per class** (perfectly balanced, 5 classes) |
| Filenames | `<class>_<5-digit index>.png` (e.g. `crack_00000.png`) |
| Folder layout | `train/<class>/<file>.png` |

### What is NOT in this dataset

* **No annotation files** — no bounding boxes, no masks, no COCO/VOC/JSON/CSV sidecars.
  Only `.png` files and directory entries exist in the archive.
* **No product identifier**, serial number, or part ID.
* **No batch/lot identifier.**
* **No timestamp** of capture or production.
* **No station/machine identifier.**
* **No test split** — the archive contains a single `train/` tree only.

**Consequences for the application**

* Classification is supported (labels exist) → trained and evaluated on a real held-out split.
* *Defect localization is not supported* — no ground truth exists, so the UI never draws a
  fabricant bounding box. Instead the inspection view offers a **model-attention map**
  (gradient saliency / Grad-CAM) explicitly labelled *"model attention — not ground-truth
  localization."*
* No product or batch is displayed as if it came from the data; specimens are labelled
  *"specimen NNN (no product identifier in dataset)."*

---

## 2. Manufacturing dataset — `Manufacturing Data Shared Facility - Discrete-Event Simulation.zip` (166.5 MB)

Mendeley dataset `3rw227zxt7` (v2). Three Rockwell **Arena 15** discrete-event simulation
models of **the same shared-facility discrete manufacturing plant**, exported as experiment
result CSVs, plus the Arena models themselves and MATLAB surrogate-model scripts.

```
Manufacturing Data Shared Facility - Discrete-Event Simulation/
├── 3000Samplesv3.mat                     # MATLAB arrays used by the surrogate ANNs
├── Matlab Models.zip                     # 25 generated .m files (Surrogate ANN + training scripts)
├── Readme.txt
├── Model 1/  Model_1.csv, Model 1.pdf, Model 1.doe
├── Model 2/  Model_2.csv, Model 2.pdf, Model 2.doe
└── Model 3/  Model_3.csv, Model 3.pdf, Model 3.doe, ParametersFile.xls
```

### 2.1 `Model 1/Model_1.csv` — 3,000 runs × 12 columns (2 trailing blank columns dropped)

Process route (from `Model 1.pdf`): arrival → **Drilling** → **Milling** → **Assembly** → store.
All three stations: processing time `TRIA(2,3,4)`, 1 resource each (`r_Drilling`, `r_Milling`,
`r_Assembly`).

| Column | Role | Observed range |
|---|---|---|
| `Demand` | **designed input factor** (integer levels **1–20**, 150 runs per level) | 1 – 20 |
| `Total parts` | output: parts produced | 431 – 10,811 |
| `Parts per hour` | output: throughput | 18 – 450 |
| `VA Time` | value-added time (near-constant by construction) | 8.92 – 9.09 |
| `Drilling Waiting Time` | queue time | 0 – 257.6 |
| `Milling Waiting Time` | queue time | ≈ 0 – 0.00039 |
| `Assembly Waiting Time` | queue time | 0 – 244.0 |
| `Drilling Util` / `Milling Util` / `Assembly Util` | resource utilisation | 0.06–1.00 / 0.04–0.75 / 0.09–1.00 |

Data quality: **0 nulls, 0 duplicate rows.**

### 2.2 `Model 2/Model_2.csv` — 3,000 runs × 17 columns

Process route (from `Model 2.pdf`): two parallel feeds (**Drilling** and **Milling**,
`TRIA(2,3,4)`, 1 resource each) → both parts stored → forklift batch → **Assembly**
(`TRIA(2,3,4)`, 1 resource) → store.

| Column | Role | Observed range |
|---|---|---|
| `Demand` | **designed input factor**, integer levels **1–20** | 1 – 20 |
| `Entities In Part 1` / `Part 2` | arrivals per part stream | 659 – 15,460 / 639 – 15,820 |
| `Part 1 VA Time` / `Part 2 VA Time` / `Assembly Time` | processing time | ≈ 3.0 (by construction) |
| `Drilling Queue Time` / `Milling Queue Time` / `Assembly Queue Time` | queue time | 0–71.0 / 0–3.34 / 0–2.94 |
| `Part 1 Storage Time` / `Part 2 Storage Time` | storage (WIP) time | 0.0008–239.9 / 0.0002–297.7 |
| `Part 1 Stored` / `Part 2 Stored` | **WIP count** | 0 – 5,925 / 0 – 6,231 |
| `Entities Out` | output: assembled parts | 637 – 9,601 |
| `Drilling Utilization` / `Milling Utilization` / `Assembly Utilization` | utilisation | 0.05–1.00 / 0.03–0.82 / 0.07–1.00 |

Data quality: **0 nulls, 0 duplicate rows.**

### 2.3 `Model 3/Model_3.csv` — 605,620 runs × 78 columns (311 MB)

This is the **shared-facility** model: one plant, 4 SKUs competing for shared stations.

Process route and capacities (`Model 3.pdf` + `ParametersFile.xls`):

```
Coil arrival → BLANKING (1 × r_Blanking, NORM(900,30) + v_Demand×2 s; batch to 2000 kg)
   → FORKLIFT (1 unit, velocity 5; transport 180 s / 60 s by leg)
   → PRESS 1..4 by SKU (each NORM(5,0.1), capacity 2 each)
   → ASSEMBLY CELL 1..4 (shared facility)
        SKU1 → Cell 1 only            capacity Cell 1 = 8
        SKU2 → Cells 1, 2, 3          capacity Cell 2 = 2
        SKU3 → Cells 3, 4             capacity Cell 3 = 2
        SKU4 → Cells 1, 2, 4          capacity Cell 4 = 4
        assembly time by SKU: SKU1 NORM(25,.1) SKU2 NORM(15,.1) SKU3 NORM(23,.1) SKU4 NORM(17,.1)
   → PAINT (2 conveyors, constant 5400 s) → QUALITY CHECK (1 resource, TRIA(50,55,60))
   → WAREHOUSE 1..4 queues
Load/unload at every leg: NORM(300,30) s.  All times in seconds.
```

Column groups:

| Group | Columns | Example observed range (mean) |
|---|---|---|
| Run marker | `Time_Now` | **constant 24 in all 605,620 rows** → end-of-run snapshot, **not** a time series |
| Station utilisation (12) | `Blanking_Util`, `Press1..4_Util`, `Cell1..4_Util`, `Paint1/2_Util`, `Quality_Util`, `Forklift_Util` | Blanking 0.853, Press ≈0.436, Cell1 **0.867**, Cell2 0.700, Cell3 0.664, Cell4 0.814, Quality 0.436, Forklift 0.608 |
| Queues / WIP (17) | `Blanking_Queue`, `Blanking_SKU1..4_Queue`, `Press1..4_Queue`, `Cell1..4_Queue`, `Warehouse1/2/3/4_Queue`, `Quality_Queue`, `Paint*_Queue`, `Forklift_*_Queue` | Blanking 62.5, Press1 72.3 (max 297.8), Quality 47.9, Warehouse queues 19–98; **`Cell1..4_Queue` are 0 in every row** |
| Per-SKU produced (4) | `c_Cycle1..4` (total per SKU; SKU1 is Cell-1-only so `c_Cycle1 == c_Cell1_SKU1`) | 14.7k–40.1k (mean) |
| Cell × SKU produced (16) | `c_Cell1..4__SKU1..4` | only the documented routing cells are non-zero |
| Total output | `c_TotalProducts` | mean **54,747**, range 50,540 – 58,653 |
| Time breakdown per SKU (20) | `SKU1..4_VA_Time / NVA_Time / Transport_Time / Wait_Time / Other_Time` | — |
| Warehouse queue | `Blanking_Queue` (also in queue group) | 50.0 – 78.6 |

Data quality (measured):

* **37 null cells** — `Blanking_Queue` (2), and 1–2 nulls in the per-SKU time-breakdown columns.
  The application counts and reports these instead of silently dropping them.
* **4 duplicated rows** (indices 13417, 22886, 73573, 147728, 228952, 271974, 491626, 528604).
* Rows are **independent replications**: lag-1 autocorrelation of `c_TotalProducts` = 0.0004,
  correlation with row index = −0.0017, and no block structure in the 4 MAT predictors.
  → **There is no within-run timeline in this dataset.**

### 2.4 `3000Samplesv3.mat` (34.8 MB)

MATLAB v5 file (created 2018-10-14) holding arrays used by the surrogate ANN scripts.

| Variable | Shape | Meaning |
|---|---|---|
| `Predictors` | (605,620, 4) | 4 numeric columns used by the c1s2 surrogate ANN |
| `Responses` | (605,620, 8) | the 8 cell×SKU outputs the surrogate ANNs predict |
| `Regression` | (605,620, 12) | `Predictors`(4) concatenated with `Responses`(8) — verified exact |
| `Responsec1s2 … Responsec4s4` | (605,620, 1) each | per-cell/SKU targets |
| `Model1Predictors/Response/Answer`, `Model2*`, `Model3*` | (3,000, n) | the 3,000-sample surrogate training subsets |
| `Model1/2/3` | opaque MATLAB objects | serialised model objects — not readable |

Verified cross-references to `Model_3.csv` (exact match over all 605,620 rows):

```
Responses[0] = c_Cell1__SKU2   Responses[4] = c_Cell3__SKU2
Responses[1] = c_Cell1__SKU4   Responses[5] = c_Cell3__SKU3
Responses[2] = c_Cell2__SKU2   Responses[6] = c_Cell4__SKU3
Responses[3] = c_Cell2__SKU4   Responses[7] = c_Cell4__SKU4
Predictors[0] = c_Cell1_SKU1
```

**`Predictors[1..3]` match no column in `Model_3.csv` and carry no metadata** anywhere in the
dataset. They are strongly (monotonically) correlated with `Press1..4_Util` (|r| ≈ 0.99), but
their identity and units are **not documented**. The application therefore reports them as
*"undocumented model counters"* and **never** presents them as experimental factors.

### 2.5 What the Arena models contain but the exports do NOT

Scanning `Model 1/2/3.doe` shows the models are configured with Arena **costing** constructs:
`Costing_Use`, `V_VACost`, `V_NVACost`, `V_TranCost`, `V_WaitCost`, `V_OtherCost`,
`V_AnyCost`, `InitVACost/InitNVACost/InitTranCost`, `Holding Cost / Hour`, `Cost to Duplicates`.
`Model 3.doe` also contains scrap logic (`Scrap subassemblies with defects`,
`Scrap brackets with defects`).

**None of these appear in any exported CSV.** There are no cost rates, no currency, no scrap
counts, no rework counts and no defect counts in the data.

- ⇒ **Economic impact cannot be calculated from the dataset.** The UI says exactly that, and
  only computes money when the engineer supplies their own labelled rate card (always marked
  *user-supplied, not from dataset*).
- ⇒ Scrap/rework volumes are likewise not measurable; they are shown as *not available*.

---

## 3. Figures the application is allowed to state (dataset-derived)

| Figure | Source | Status |
|---|---|---|
| Image class labels, class balance | `train.zip` | available |
| Image classification accuracy / F1 | measured on held-out split by `backend/training/train_vision.py` | produced by this repo, reproducible |
| Station utilisation & queues | Models 1–3 CSVs | available |
| WIP (stored parts) | Model 2 `Part 1/2 Stored`; Model 3 queue columns | available |
| Throughput | `Parts per hour`, `Entities Out`, `c_TotalProducts`, `c_Cycle1..4` | available |
| Cycle/queue/wait time | Models 1–3 CSVs | available |
| Capacity / resources | `Model 3.pdf`, `ParametersFile.xls` | available (model documentation) |
| Demand → output response | Models 1–2 `Demand` factor | available (real designed factor) |
| Scrap, rework, downtime, defect rate per batch | — | **not available** |
| Cost, currency, profitability | — | **not available** |
| Within-run timeline, batch ID, part ID | — | **not available** |

---

## 4. Cross-dataset linkage — measured result

| Key | Image dataset | Simulation dataset |
|---|---|---|
| product / part ID | ✗ | ✗ (SKU is a *type*, not a part) |
| batch / lot ID | ✗ | ✗ |
| timestamp | ✗ | ✗ (`Time_Now` constant 24) |
| station ID | ✗ | ✓ (encoded in column names) |
| run ID | ✗ | ✗ (anonymous replications) |

**Shared keys: NONE.** `linkage.joinable = false`.

There is no evidence-backed edge between *"a surface defect class"* and *"a station/run
deviation"*. The Failure Propagation Graph therefore renders two provably separate domains and,
where they are connected at all, the edge is drawn as a **declared assumption** (dashed,
labelled *advisory / no shared key*), stored in the database with its author and timestamp.
The graph legend distinguishes **observed** edges (computed from the data) from **assumed**
edges (engineer-declared) so the two can never be confused.
