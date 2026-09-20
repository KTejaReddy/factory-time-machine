# DATASET_EXPLANATION.md

A plain-language description of **every dataset in this project**: what is in it, what
each field means, what it can and cannot be used for, and exactly how the application
uses it.

Everything here was **measured from the files in this repository** — nothing is assumed.
The precise measurements (column lists, row counts, null counts, cross-checks) live in
[`DATASET_SCHEMA.md`](DATASET_SCHEMA.md); reproduce them with:

```bash
python scripts/inspect_datasets.py --json     # writes data/cache/dataset_report.json
```

---

## Dataset 1 — `train.zip` (surface-defect images)

```
Dataset:           train.zip  (300.9 MB, supplied in the project root)
What it contains:  photographs of part surfaces, filed by defect class
Number of images:  12,000 PNGs, 256 x 256 pixels
Labels:            crack · hole · normal · rust · scratch  (2,400 images each)
Important fields:  folder name = label; filename = <class>_<5-digit index>.png
```

**What it can be used for**

* Training and evaluating an image classifier (5-class classification).
* Measuring classification accuracy, macro-F1 and a per-class confusion matrix on a
  real held-out split.
* Robustness probing with external images (`data/external_test/`, see below).

**What it cannot be used for**

* **No defect location.** The archive contains only `.png` files — no bounding boxes,
  no masks, no COCO/VOC sidecars. The application therefore *never* draws a defect box.
  What it shows instead is a **model-attention map** (where the network looked),
  explicitly labelled as model behaviour, not ground truth.
* **No product, batch, station or timestamp identifier** on any image, so an image can
  never be joined to the simulation data (see "Can the two datasets be joined?").
* **No defect rate per batch or per station** — labels exist only per image, and the
  production data has no quality outcome at all.
* **No real factory photos beyond these five conditions** — e.g. no oil residue, no
  weld spatter, no paint runs. An unfamiliar condition has no class here.

**How the application uses it**

* `backend/app/services/catalog.py` indexes the archive **without extracting it**
  (a ZIP central-directory scan) and serves images and thumbnails straight from it.
* `backend/app/services/vision_model.py` decodes every image **once** into
  `data/cache/vision_images_96.npy` (uint8, 12,000 × 96 × 96) — the archive itself is
  never unpacked into the repository.
* `backend/training/train_vision.py` trains the CNN on this cache and writes measured
  metrics to `data/cache/vision_metrics.json`. Only a trained model produces
  predictions; with no checkpoint the API says so instead of inventing output.
* Every 6th file of each class is the validation split (deterministic, ≈16.7 %), so the
  reported accuracy is measured on images the model never trained on.

**Measured training result (this repository)**

Accuracy 0.9995, macro-F1 0.9995 on 1,996 held-out images — see
`data/cache/vision_metrics.json`. The model is a small 6-conv-layer CNN; the near-perfect
score is expected on this dataset because the five classes are visually distinct and
clean, and it should **not** be read as industrial-grade reliability. The external
image campaign (`EXTERNAL_IMAGE_TEST_REPORT.md`) exists precisely to show where it fails.

---

## Dataset 2 — `Manufacturing Data Shared Facility - Discrete-Event Simulation.zip`

```
Dataset:           Arena 15 discrete-event simulation exports (166.5 MB)
                   Mendeley dataset 3rw227zxt7 v2
What it contains:  results of three Rockwell Arena simulation models of one shared
                   manufacturing facility, plus the model files and MATLAB surrogates
Number of records: Model 1: 3,000 runs x 12 columns
                   Model 2: 3,000 runs x 17 columns
                   Model 3: 605,620 runs x 78 columns
Labels:            none — this is numeric experiment output, not labelled data
Important fields:  per-station utilisation (*_Util), queue/WIP counters (*_Queue),
                   throughput counters (c_TotalProducts, c_Cycle1..4, Entities Out,
                   Parts per hour), and Demand (a designed input factor in Models 1-2)
```

> **What is a discrete-event simulation export?** Arena is software that plays a virtual
> factory forward in time: parts arrive, wait in line, get processed, and move on. Each
> row in these CSVs is the summary of **one simulated production run**, not one moment of
> a real factory. Each row is an independent replication of the model.

### Model 3 — the shared facility (the main dataset)

```
Route:      Coil → Blanking → Forklift → Pressing (4 presses) → Assembly (4 cells)
            → Paint (2 conveyors) → Quality check → Warehouse
SKUs:       4 product types, each routed to specific assembly cells
Capacity:   documented per station in Model 3.pdf (e.g. Cell 1 = 8, Cells 2/3 = 2, Cell 4 = 4)
Horizon:    Time_Now = 24  (constant in every row → 86,400 s = one production day)
```

Key fields (with the honest caveats — these caveats are why the UI looks the way it does):

| Field group | Meaning | Caveat that matters |
|---|---|---|
| `Blanking_Util`, `Press1..4_Util`, `Cell1..4_Util`, `Paint1/2_Util`, `Quality_Util`, `Forklift_Util` | fraction of the station's capacity in use during that run | 12 columns; the direct measure of how loaded each station is |
| `*_Queue` (17 columns) | Arena queue counters at run end | **Not a time-weighted mean waiting length** — the app treats any comparison against it as a different quantity and says so |
| `Cell1..4_Queue` | assembly waiting | **0 in every row** — waiting shows up in the Warehouse queues instead |
| `c_Cell1__SKU1..4` … `c_Cell4__SKU1..4` (+ single-underscore `c_Cell1_SKU1`) | parts produced by cell × SKU | the basis of measured throughput; note the inconsistent single/double underscore in the export |
| `c_TotalProducts` | products after the quality check | readme: "total number of products produced after a quality check" |
| `c_Cycle1..4` | model counters "cycles SKU1..4" | the export **does not define the unit** — displayed as a counter, never used as throughput |
| `Time_Now` | constant **24** in all 605,620 rows | an end-of-run snapshot, **not a timestamp** |
| `SKU*_VA_Time / NVA_Time / Transport_Time / Wait_Time / Other_Time` | per-SKU time breakdown | 1–2 nulls exist; counts are reported, not hidden |

**What it can be used for**

* Per-station utilisation, queue and WIP statistics across 605,620 simulated runs.
* Comparing operating regimes (runs that produced few parts vs many).
* Finding statistically unusual runs (PCA + Mahalanobis).
* Ranking the capacity-constrained (bottleneck) station.
* Re-simulating the documented route under a hypothetical change, **validated against the
  export's own utilisation columns**.
* Checking whether the documented plant description (from the PDFs) reproduces the data —
  see the calibration report.

**What it cannot be used for**

* **No timeline.** Rows are independent replications (lag-1 autocorrelation of the output
  counter is 0.0004). "Earliest divergence" is therefore ordered by the **documented
  process route**, never by clock time, and is labelled that way everywhere.
* **No defects, scrap, rework or downtime** anywhere in any export. So the app cannot
  measure a defect rate, cannot attribute a bottleneck to a failure mode, and cannot
  simulate scrap reduction.
* **No cost, price or currency** anywhere. The Arena models *configure* costing constructs
  (`V_VACost`, `Holding Cost / Hour`, …) and scrap logic, but **none of it is exported**.
  Money figures therefore require a user-supplied rate card; the app refuses to invent one.
* **No part, batch, lot or run identifier** — a run cannot be traced to a physical
  production event.
* **No arrival schedule**, so the re-simulation admits the measured volume uniformly
  rather than reproducing Arena's bursty batch releases. Queue *lengths* are therefore
  compared against an unmodified run of the same engine, not against the export.
* **Assembly Cell 4 contradicts its own documentation**: the exported utilisation implies
  a capacity of ~5 against the documented 4. That contradiction is reported as a finding
  on the Dashboard, not tuned away.

**How the application uses it**

* `catalog.py` extracts only the CSVs it needs into `data/raw/` and caches them as
  parquet in `data/cache/` (first start ~12 s afterwards instead of re-parsing 311 MB).
* `analytics.py` — utilisation-identity calibration, PCA + Mahalanobis anomaly scoring,
  regime contrast, demand response, stage association.
* `bottleneck.py` — the weighted constraint ranking (utilisation 0.80, WIP/queue 0.12,
  capacity 0.08) plus the load-headroom arithmetic.
* `forensics.py` — route-ordered divergence profiles, evidence chains, the propagation graph.
* `simulation.py` — the pure-Python discrete-event re-simulation and the demand-response
  interpolation engine.
* `economics.py` — measures quantities; multiplies them only by user-supplied rates.

### Models 1 and 2 — the smaller lines

```
Model 1:  Drilling → Milling → Assembly, 1 resource each, TRIA(2,3,4) s service
          3,000 runs; designed factor: Demand (integer levels 1-20)
          outputs: Total parts, Parts per hour, VA Time, waiting times, utilisations
Model 2:  Drilling ∥ Milling → storage → forklift batch → Assembly
          3,000 runs; designed factor: Demand (1-20)
          outputs: Entities Out, Stored counters (WIP), queue times, utilisations
```

These are the **only** models with a real designed input factor, which is why the What-If
"demand change" engine uses them: a demand change is answered by reading the measured
demand→throughput curve and a cross-validated surrogate (out-of-fold R² is reported),
not by simulation.

---

## Dataset 3 — `3000Samplesv3.mat` (MATLAB surrogate arrays)

```
Dataset:          3000Samplesv3.mat
What it contains: MATLAB v5 arrays used by the published surrogate ANN scripts
Number of records: 605,620 rows per predictor/response array
Important fields: Predictors (605620 x 4), Responses (605620 x 8),
                  Regression (605620 x 12 = the two concatenated), per-cell targets
Labels:           none
```

**Verified cross-references** (exact over all 605,620 rows): `Responses[0..7]` equal the
`c_CellX__SKUY` columns of `Model_3.csv`, and `Predictors[0]` equals `c_Cell1_SKU1`.

`Predictors[1..3]` match **no column** in the CSV and carry no metadata anywhere in the
dataset. They are strongly monotonically correlated with `Press1..4_Util` (|r| ≈ 0.99) but
their identity and units are undocumented, so the application reports them as
*undocumented model counters* and never as experimental factors.

**How the application uses it:** the Datasets page shows the variable shapes, the verified
cross-references and the undocumented arrays. No analytics are built on the undocumented
predictors.

---

## Dataset 4 — `data/external_test/` (test-only, created by this application)

```
Dataset:          external test images (uploads + generated probes)
What it contains: images the model was NOT trained on
Number of images: as many as you upload/generate (capped at 500; oldest generated
                  images are pruned first)
Labels:           NONE. There is no ground truth for these images, and the app
                  does not pretend otherwise.
```

**The separation rule (enforced in code, not by convention):**

* the training pipeline reads `train.zip` only (`vision_model.build_cache()` opens the
  archive itself);
* this directory is not on any path that touches training data, labels or model weights;
* every stored record carries `training_data: false`, and the API exposes a
  `GET /api/external-images/info` endpoint stating the rule;
* the UI prints **"External Test Image — Not Used for Training"** on every result.

**What it is for**

* seeing how the model behaves on an image it has never seen — including images that are
  not factory parts at all;
* measuring robustness to brightness, rotation, flip, blur, noise and contrast changes;
* demonstrating the out-of-distribution warning (see below).

**What it is NOT**

* **Not real industrial data.** Images marked `nonfactory` are drawn shapes (a face, a
  car, a tree, a phone, random noise) and `variation` images are transformed archive
  images. Neither represents a real factory condition, and neither has a correct answer
  to score against.
* **Not an accuracy benchmark.** Because there are no labels, no accuracy may be quoted
  from this directory. `EXTERNAL_IMAGE_TEST_REPORT.md` records *behaviour*, not accuracy.

---

## Can the two datasets be joined? (measured: no)

| Key | Image dataset | Simulation dataset |
|---|---|---|
| product / part ID | ✗ | ✗ (SKU is a product *type*, not a part) |
| batch / lot ID | ✗ | ✗ |
| timestamp | ✗ | ✗ (`Time_Now` is constant 24) |
| station ID | ✗ | ✓ (encoded in column names) |
| run ID | ✗ | ✗ (anonymous replications) |

**Shared keys: none.** `linkage.joinable = false`.

There is therefore no data-backed edge from a *surface defect class* to a *station
deviation*. Where the propagation graph connects those two domains at all, the edge is
drawn **dashed** and labelled *engineer-declared assumption*, stored with its author and
timestamp. No text in the application describes a defect-to-station link as measured.

---

## Summary: what the application may and may not state

| Figure | Source | Status |
|---|---|---|
| Image classes, class balance, model accuracy/F1 | `train.zip` + this repo's training run | ✅ available, reproducible |
| Station utilisation, queues, WIP, throughput | Model 3 (and 1–2) CSVs | ✅ available |
| Capacity / processing times / routing | `Model 3.pdf`, `ParametersFile.xls` | ✅ documented |
| Demand → output response | Models 1–2 `Demand` factor | ✅ designed factor |
| Cost, price, currency, profitability | — | ❌ not available (rate card required) |
| Scrap, rework, downtime, defect rate per batch | — | ❌ not available |
| Within-run timeline, batch ID, part ID | — | ❌ not available |
| Defect location (bounding box / mask) | — | ❌ not available (attention map only) |
| Anything about an external test image's "true" class | — | ❌ no ground truth exists |
