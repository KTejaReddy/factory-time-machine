# SYSTEM_EXPLANATION.md

**The beginner-friendly master explanation of Factory Time Machine.**

Read this if you have never seen the project (and have no machine-learning background).
It explains what the system does, what data enters it, what happens to that data, how each
algorithm works in plain language, and what a user actually sees — in the order the system
itself works.

Companion documents:

| Document | Answers |
|---|---|
| `DATASET_EXPLANATION.md` | What is actually inside the data files |
| `ALGORITHM_REFERENCE.md` | Every algorithm: input, method, output, limits |
| `FUNCTION_REFERENCE.md` | Function-by-function walkthrough of the source |
| `API_REFERENCE.md` | Every HTTP endpoint |
| `DATASET_SCHEMA.md` | Exact measured column/row/null figures |
| `DATASET_GUIDE.md` | How to prepare your own CSV, column by column |
| `MULTI_DATASET_ARCHITECTURE.md` | Case files: one per dataset, and how they stay separate |
| `REPAIR_ANALYSIS.md` | Interventions, where the rates come from, cheapest supported effective repair |
| `FINAL_REPORT_FORMAT.md` | What the downloadable report contains |
| `EXTERNAL_IMAGE_TEST_REPORT.md` | What happened when the model met unfamiliar images |
| `TEST_REPORT.md` | What was tested and what passed |
| `SECURITY_AUDIT.md` | Secret handling, input safety, AI safety |
| `FINAL_AUDIT.md` | Feature-by-feature status of the whole application |

---

## 1. What the project does

A defect classifier answers one question: *"what does this image look like?"*

Factory Time Machine answers the next five:

```
"What is wrong?"                 →  inspect an image, see a prediction + how sure the model is
"Why might it be happening?"     →  find where the process first diverged, along the real route
"How is it affecting production?" →  see the constraint, the WIP and the propagation chain
"What is the impact?"            →  measure it in parts (money only if you supply rates)
"What if we change it?"          →  re-simulate the line with your change, validated against the data
"Do I agree with the finding?"   →  confirm / reject / flag for review; the decision is stored
```

It is a **decision-support tool for humans**. It controls no machine, no PLC, no camera,
no robot. Every recommendation and simulation is advisory.

## 2. Why it exists

Two failure modes are common in manufacturing analytics, and this project is built
specifically against both:

1. **Disconnected evidence.** "This image looks like a crack" and "station 1 is 87 % busy"
   live in different systems and nobody joins them.
2. **Confident fabrication.** When the data cannot support an answer, the usual behaviour
   is to quietly produce a plausible number anyway. This project does the opposite: where
   a variable does not exist, the feature is **disabled and explains itself**.

The strongest example: **no dataset contains a cost, price or currency**. So the economics
view computes nothing until you supply your own rate card — and then labels every line with
where its quantity came from and where its rate came from.

## 3. What data enters the system

```
        data/raw/*.csv  +  *.mat  +  *.pdf        train.zip  (12,000 images)
        (Arena simulation exports)                 (5 surface classes)
                    │                                     │
                    └──────────────┬──────────────────────┘
                                   ▼
                    Factory Time Machine backend
```

* **12,000 labelled images** — crack, hole, normal, rust, scratch (2,400 each).
* **611,620 simulated production runs** across three Arena models — the shared-facility
  model (Model 3) has 605,620 rows × 78 columns.
* The datasets **share no key** (no product, batch, station or timestamp on the image side;
  no image reference on the simulation side). Everything about that is in
  `DATASET_EXPLANATION.md` §"Can the two datasets be joined?".

## 4. What happens to the data

```
RAW FILES
   │  load: CSV/parquet cache in data/cache (first run ~12 s, later ~6 s)
   ▼
DATAFRAMES (in memory, numpy/pandas)
   │  clean: drop empty columns; COUNT missing cells (37), COUNT duplicate rows (4);
   │         never silently drop either
   ▼
FEATURES
   │  utilisation columns, queue columns, cell×SKU counters, time breakdowns
   ▼
ANALYTICS  ────────────────┬───────────────┬──────────────┐
   │                       │               │              │
   ▼                       ▼               ▼              ▼
calibration           anomaly + regime   forensics     bottleneck +
(utilisation          (PCA + Mahalanobis) (route order,  headroom
 identity check)                           evidence)     (weighted score)
   │                       │               │              │
   └───────────┬───────────┴───────┬───────┘              │
               ▼                   ▼                      ▼
        AI NARRATIVE        PROPAGATION GRAPH       ECONOMICS (gated)
        (LLM *phrases*      (observed vs assumed    (only with a user
         backend numbers)    edges, labelled)        rate card)
               │                   │                      │
               └───────────┬───────┴──────────┬───────────┘
                           ▼                  ▼
                    WHAT-IF SIMULATION   HUMAN REVIEW (SQLite)
                    (validated against    confirm / reject /
                     the export)          needs review
                           │
                           ▼
                        REACT UI
```

Two rules hold across the whole pipeline:

* **Numbers come from Python, never from the LLM.** The language model receives a compact
  JSON evidence bundle and is only allowed to phrase it.
* **Correlation is reported as association.** Every forensic case carries an explicit
  causal caveat; the propagation graph separates *observed* from *assumed* edges.

## 5. How the AI (vision) works

See `ALGORITHM_REFERENCE.md` §1 for the full specification. In short:

```
IMAGE → grayscale + resize 96×96 → normalise → CNN (6 conv layers)
      → 5 class probabilities → take the best → average with the mirrored image (TTA)
      → CONFIDENCE + TTA AGREEMENT + OOD DISTANCE → known / uncertain → Grad-CAM highlight
```

* **CNN** — a small purpose-built convolutional network (not a borrowed pretrained model),
  trained from scratch on the 12,000 supplied images. Architecture: two conv blocks at
  32 channels, two at 64, two at 128, each with batch-norm and ReLU, max-pooling between
  blocks, global average pool, dropout 0.3, linear classifier to 5 classes.
* **Confidence** — the model's probability for the winning class, averaged over the image
  and its horizontal mirror.
* **TTA agreement** — how much the image and its mirror agree (1.0 = identical answer).
  Low agreement means the model is guessing.
* **Uncertain flag** — set when confidence < 0.60, **or** flip agreement < 0.60, **or** the
  image sits far outside the training distribution (see next).
* **Out-of-distribution (OOD) distance** — the distance from this image to the *nearest
  training image* in the network's own 128-dimensional feature space. This was added after
  the external-image campaign showed the CNN happily calling a drawing of a face "crack at
  100 %": a classifier has no "unknown" class, and flip-agreement cannot catch an
  unfamiliar image because its mirror is unfamiliar too. The embedding distance can.
  Calibration: training images and mild variants measure ≤ 0.029; rotated, non-factory and
  noise images measure ≥ 0.040; the threshold is 0.030.
* **Grad-CAM attention** — highlights which image regions most influenced the decision,
  binned to an 8×8 grid. It is presented as *"where the network looked"*, never as a defect
  location, because the dataset contains no location labels at all.

## 6. How defect detection works (the honest version)

The system can say:

> "Prediction: rust / corrosion — confidence 79 %. Flip agreement 97 %. Outside the
> training distribution: yes (0.041 vs 0.030 threshold). Treat as a robustness probe."

It cannot say *where* on the part the defect is, and it does not: there are no bounding
boxes or masks in the dataset, so no defect location is ever reported. This limitation is
printed next to the attention map on every result.

## 7. How anomaly detection works

```
605,620 simulated runs × (12 utilisation + 17 queue + 20 counter columns)
   → log-transform the counters (they span orders of magnitude)
   → standardise every column
   → PCA: compress to at most 6 components (the elgen-directions of normal variation)
   → Mahalanobis distance in that space = "how unusual is this run, given how
     normal runs themselves vary"
   → threshold = the 99.5th percentile of the sample  → ~0.5 % flagged
```

Plain language: each simulated run is plotted in a compressed "process state" space;
runs that sit far from the crowd are unusual. The app reports the top runs **and the
features that drove each one** (per-feature robust z-scores), so the flag can be argued
with. It is never called a defect — no export records defects.

## 8. How root-cause evidence works

```
Choose a case  (e.g. "lost output while the plant was busy")
   → for every station along the documented route, measure how far this selection sits
     from the population:
        group cases  → Cohen's d (standardised difference of means)
        single run   → robust z-score (median/MAD, because queues are heavy-tailed)
   → order the stations by the DOCUMENTED ROUTE, not by time (there are no timestamps)
   → first station past the threshold = "first divergence"
   → count how many later stations also diverged = coherence
   → confidence = a transparent heuristic on 4 stations' worth of evidence (max 0.95),
     and every point of it is listed in `confidence_basis`
```

The output is a chain of **Finding + Evidence + Confidence + Limitations**, with the
evidence list naming exactly what was measured and on which column. The word "cause" is
never used; the case carries a causal caveat, a baseline definition, and the data-quality
notes (37 missing cells, 4 duplicate rows, CellX_Queue ≡ 0).

## 9. How the propagation graph works

```
process problem → station → WIP/queue → downstream effect → production impact → economic impact
```

* Nodes are the documented stages (Blanking → Forklift → Pressing → Assembly → Paint →
  Quality → Warehouse buffers), the ranked bottleneck candidate, the throughput-loss
  exposure, the (gated) economic node, and the five vision defect classes.
* **Observed edges** (solid) come from the data: the documented route order, plus
  *partial correlations* between an upstream station's utilisation and a downstream
  station's queue while controlling for the press/cell/forklift load profile.
* **Assumed edges** (dashed, purple) are engineer-declared defect-class → station links.
  They exist because the datasets share no key, are stored with author and timestamp, and
  are drawn differently so they can never be mistaken for measured relationships.
* The economic node is greyed out with the reason ("no cost value exists in any export").
* Clicking any node shows the data behind it: capacity and where it came from, utilisation
  and whether it was exported or recomputed, queue mean/max, headroom, rank components.

## 10. How bottleneck detection works

A bottleneck is the capacity-constrained station. Three measured quantities are combined
with **published weights**:

```
score = 0.80 × utilisation (normalised to the busiest measured station)
      + 0.12 × WIP/queue rank across stations
      + 0.08 × capacity constraint (1/(1+capacity), min-max normalised)
```

Utilisation dominates deliberately: the station with the highest long-run utilisation is
the binding constraint, and the other two terms only break near-ties. The weights, the
components, the normalisation reference and a written justification for every candidate
are all returned by the API, so the ranking can be challenged. For the assembly cells the
WIP term uses the warehouse buffer queues, because `CellX_Queue` is 0 in every row — and
the interface says so rather than reporting a misleading zero.

**This is arithmetic, not AI.** The UI labels it a weighted formula. Alongside it,
`load_headroom = 1/utilisation − 1` states how much extra load the station can absorb
before it saturates.

## 11. How economics work

```
Dataset quantities           User-supplied rates         Lines produced
────────────────────         ───────────────────         ─────────────
mean WIP (queue columns)  ×  holding cost per unit/hour → holding cost over the horizon
output spread vs p99      ×  margin per unit             → unrealised output
(no scrap count exists)   ×  scrap cost per unit         → "cannot be evaluated" line
(no rework count exists)  ×  rework cost per unit        → "cannot be evaluated" line
(no downtime column)      ×  downtime cost per hour      → "cannot be evaluated" line
```

With no rate card the API returns `available: false`, the precise reason, the list of
missing variables and the Arena costing constructs that exist in the model files but are
never exported. With a rate card, every line still records `quantity_source` (dataset) and
`rate_source` (user-supplied). Money is never invented.

## 12. How the simulation works

> "The computer creates a virtual production line, sends products through the stations,
> lets them wait when a station is busy, and measures what happens."

Implementation (pure Python, no SimPy): a discrete-event engine with a priority queue of
events. Parts are **scheduled as arrival events** across the horizon; each station has a
capacity (servers); a part that finds a free server starts immediately, otherwise it waits
in a real queue and is promoted when a server frees up.

Calibrations, all derived from the export rather than invented:

| Parameter | How it is set |
|---|---|
| release volume | measured mean assembled parts per run |
| SKU → cell routing shares | measured from the `c_CellX__SKUY` counters |
| quality-check batch size | measured parts ÷ measured quality throughput |
| paint conveyor delay | back-solved from the exported conveyor utilisation (the documented 5400 s/part contradicts the export, and that contradiction is reported) |

Then it is **validated against the data**: simulated utilisation per station is compared
with the exported utilisation columns, and the residual is shown. Seven stations reproduce
the export within 5 %; Assembly Cell 4 does not, because the export's utilisation implies a
capacity of ~5 against the documented 4. That is surfaced as a finding, not hidden.

Scenarios naming a station the engine cannot adjust (e.g. blanking, or a paint conveyor
modelled as unbounded) are **rejected with a reason**, because a silent no-op would read
as "this change had no effect".

## 13. How the AI API works

```
backend computes evidence  ──►  LLM receives compact JSON evidence  ──►  response validated
   (stats, ranks, sims)           (never the raw datasets)                (strict contract)
                                                                              │
                                        deterministic template engine  ◄──────┴──►  UI
                                        (used when no key, on error or rate limit)
```

* Provider-agnostic: any OpenAI-compatible `/chat/completions` (OpenAI, Groq, Azure,
  OpenRouter, local Ollama/vLLM/LM Studio), plus an Anthropic wire format.
* The response must validate against `AIFinding`: `finding`, `evidence[]`, `confidence`
  (0–1), `limitations[]`, `recommendation`. Output that looks like an instruction (SQL,
  shell) is neutralised; model text is never executed and never becomes a database command.
* Rate limited (`AI_MAX_CALLS_PER_HOUR`), cached, logged, and always degradable: with no
  key, a failing provider, or a rate limit, a deterministic template engine writes the
  finding from the **same** evidence and labels itself `source: "deterministic"`.
* The UI always shows which engine answered, and when a provider call failed it shows the
  failure reason rather than silently pretending nothing was attempted.

> **Note from this audit:** the checked-in `.env` pointed at a Groq model that Groq has
> since *decommissioned* (`llama3-70b-8192`), so every AI call was failing with HTTP 400 and
> silently falling back. The model was repointed to a currently-served one
> (`openai/gpt-oss-120b`) and the live path re-tested end to end. This is exactly the
> failure the fallback design survives — but it should not have been invisible, and the
> `fallback_reason` field now reports provider errors.

## 14. How human feedback works

```
Confirm  ✓      Reject  ✕      Needs review  ⚠
        └────────────┬────────────┘
                     ▼
        SQLite row: finding id, kind, the exact payload the engineer saw,
        decision, note, engineer, UTC timestamp
```

**Feedback is stored. No model learns from it.** Nothing in this build consumes feedback
to retrain or recalibrate anything, and the API response says so verbatim
(`model_retrained: false`). Feedback exists for audit and for a future supervised retraining
pipeline. Claiming otherwise would be false.

## 15. How every dataset gets its own case file

```
USER UPLOADS A CSV
        ↓
  profiler (what is in this file?)   →   capability map (what could it support?)   →   NEW CASE FILE
        ↓                                                                                  ↓
  RUN ANALYSIS: quality · process anomalies · production constraint · forensic finding ·
                propagation · economics · repairs · AI finding
        ↓
  SAVED against this dataset  →  AI answer  →  repair ranking  →  report (MD / CSV / JSON)

DATASET A → case A        DATASET B → case B        (uploading B never overwrites A)
```

Every dataset — the three supplied simulation exports, the image archive, and each CSV you
upload — gets one **case file**: a stable id (`ds-factory-batch-a`), an upload date, a status, a
profile, a capability map, and its own saved analysis, rate card, repair ranking, scenario
history, AI finding, engineer feedback and downloadable report. A second upload creates a second
case file; the header selector switches the active dataset and every page re-fetches against it,
so two datasets can show two different bottlenecks, two different cost situations and two
different AI answers without ever mixing them.

The three documents that describe this layer in detail:

| Document | Read it for |
|---|---|
| `MULTI_DATASET_ARCHITECTURE.md` | the case-file model, the database schema, the isolation rules, switching and persistence |
| `REPAIR_ANALYSIS.md` | which interventions a dataset supports, where each rate comes from, and how the cheapest supported effective repair is chosen |
| `FINAL_REPORT_FORMAT.md` | what the downloadable report contains, per format, and how it stays traceable to one dataset |

Uploading your own file is described for the data-preparation side in `DATASET_GUIDE.md`.

## 16. What the user sees

```
📂 Datasets        every case file with its status and saved history (+ schema and quality)
🏠 Overview        status light, one plain-language finding, one next step
👁️ Inspect         pick a factory image  ──┐
                    or upload / generate an external test image (never training data)
🔎 Investigate     pick a problem → first divergence → evidence → limitations → review
🏭 Production      what is happening: throughput, WIP, utilisation, ranked bottleneck
🕸️ Problem Flow    click through the propagation graph node by node
💰 Economics       what it costs, with the rate each figure used — or the rate card to supply
🔧 Repairs         what can be changed, what it is worth, and the cheapest supported effective repair
🧪 What-If         change a capacity or a processing time, see current vs simulated + validation
💬 AI Help         ask a question; the backend assembles the evidence, the AI phrases it
👨‍🔧 Review          every stored decision, with the payload it was made on
📄 Reports         download the final analysis (Markdown / CSV / JSON)
```

Every technical term is available as a plain-language tooltip, and every page has a
**"How this was calculated"** block naming the actual algorithm.

## 17. The whole project as one story

```
A product image has a defect
        ↓   CNN identifies it (with confidence, flip-agreement and an OOD check)
Process analysis finds unusual behaviour
        ↓   PCA + Mahalanobis across 605,620 simulated runs
Evidence connects it with a station
        ↓   route-ordered divergence with Cohen's d / robust z, plus its limits
The failure graph shows the propagation
        ↓   observed edges from partial correlations; assumed edges dashed and labelled
Bottleneck analysis finds production pressure
        ↓   0.80 utilisation + 0.12 WIP rank + 0.08 capacity, with justification
Economic analysis estimates impact
        ↓   measured quantities × your rates (nothing at all without them)
Simulation tests a possible change
        ↓   discrete-event re-run, validated against the export's own utilisation
The AI explains the evidence
        ↓   LLM phrases backend-computed numbers, validated against a strict contract
An engineer reviews the finding
        ↓   confirm / reject / needs review — stored, never auto-trained on
```

Every arrow in that chain is implemented in this repository; none of it is decorative.
