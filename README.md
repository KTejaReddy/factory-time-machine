# Factory Time Machine - Manufacturing Forensic Decision Support

**A manufacturing forensic decision-support system that refuses to invent numbers.**

It answers a question a defect classifier cannot:

> *"Why did this batch have more defects — where did the process first diverge, how did that
> propagate, what did it cost, and what happens if we change it?"*

It ships with a documented plant — three discrete-event simulation exports and a
12,000-image defect archive — and those power the analyses. You can also add your own CSV:

```
Upload Dataset  →  Profiler (what is in this file?)  →  Capability Map (what could it support?)  →  Catalog
                                                                              ↓
                       Supplied archives  →  Inspection · Anomaly · Forensics · Propagation · Bottleneck · What-If · Economics
```

Uploaded datasets are **profiled, not analysed**: the profiler detects their stations, process
variables, cost fields and batch identifiers, and the capability map states which analyses the
file could support and why the rest are off. The calibrated engines (the discrete-event
re-simulation, the bottleneck weights, the utilisation identity) are tied to the supplied Model 3
export, and the capability map says so instead of pretending otherwise. Every recommendation,
intervention and profitability figure is *simulated / advisory*.

> Honest scope, plainly: the upload path is *profile + capability map*. Running the simulation
> engine on an arbitrary schema is a modelling project, not a feature toggle — see
> `FINAL_AUDIT.md` §3.1.

---

## 1. The problem

Manufacturing teams get two disconnected kinds of evidence:

* **Inspection output** — "this image looks like a crack, 0.93 confidence."
* **Production KPIs** — utilisation, WIP, throughput, queue times.

Neither alone tells you *when a process started behaving differently*, *whether that change had
consequences*, or *what to do about it*. And when the data cannot support a conclusion, the usual
failure mode is to quietly invent one.

This project's second, harder goal is therefore **calibrated honesty**. Where the datasets do not
contain the variable, the feature is *disabled and explains itself* rather than being faked.

## 2. The solution

A single workflow, from raw datasets through to a reviewed recommendation:

| Stage | What the system does |
|---|---|
| **Inspect** | A CNN trained on the supplied image archive classifies surface condition, reports calibrated confidence, flags uncertain cases, and shows model *attention* — never a fabricated bounding box. |
| **Anomaly & drift** | Statistical profiling of the simulation exports: robust-z against per-station baselines, anomaly scoring of individual replications, and demand-response regimes. |
| **First divergence** | Because the exports contain **no timestamps**, ordering is done along the **process route documented in the model PDFs**. The result is an *ordered divergence profile*, labelled as such everywhere. |
| **Root-cause evidence** | Every finding carries **Finding + Evidence + Confidence + Limitation**, with the evidence list enumerating exactly what was measured and where it came from. |
| **Failure propagation** | An interactive React Flow graph linking process → state → defect → scrap/rework → WIP → bottleneck → throughput loss → cost, with **observed** edges visually distinguished from **assumed** (engineer-declared) ones. |
| **Bottleneck analysis** | Constrained stations ranked on utilisation (the direct constraint measure) with WIP and capacity headroom as tie-breakers; each candidate shows *why* it was identified. |
| **Economics** | Gated. No dataset contains a price, cost, currency, scrap, rework or downtime value, so the system states that and computes nothing — unless the engineer supplies their own labelled rate card. |
| **What-if** | A pure-Python discrete-event re-simulation of the documented Model 3 route, calibrated to measured production volume and **validated against the export's own utilisation columns**. |
| **Review** | Confirm / Reject / Needs Review on any finding, stored with timestamp, decision and note. |

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ DATASETS   train.zip (12,000 images)  +  Arena DES exports   │
│            no shared key — linkage is reported as absent     │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ DATA PROCESSING   streamed ingestion, parquet/JSON caches,    │
│                   population profiling, data-quality report   │
│                   (data/cache/*, so pages never re-read CSVs) │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ AI ENGINE   CNN vision  +  robust-z / Cohen's d anomaly       │
│             +  LLM narrative over structured evidence         │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ MANUFACTURING ANALYSIS   route-ordered divergence, evidence   │
│   chains, propagation graph, bottleneck ranking, gated econ   │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ WHAT-IF SIMULATION   discrete-event re-simulation + demand    │
│                      response, validated vs the export        │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ RECOMMENDATION + HUMAN REVIEW   SQLite-backed feedback store  │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ WEB DASHBOARD   React + Tailwind industrial control-room UI   │
└──────────────────────────────────────────────────────────────┘
```

### Repository layout

```
backend/
  app/
    config.py          environment-driven settings (no hard-coded secrets)
    db.py              SQLAlchemy models: feedback, scenario runs, declared linkages
    schemas.py         pydantic contracts incl. the strict AI-output shape
    routers/           datasets · analytics · inspection · simulation · ops
    services/
      catalog.py       streamed ingestion, caching, profiling, data quality
      domain.py        the documented plant (capacities, times, SKU routing)
      analytics.py     profiling, anomaly scoring, calibration, regimes
      bottleneck.py    constraint ranking with per-candidate evidence
      forensics.py     route-ordered divergence, evidence chains, propagation graph
      economics.py     capability-gated economic assessment
      simulation.py    pure-python DES + observed demand-response engine
      vision.py        inference, uncertainty, attention maps
      vision_model.py  the CNN and its deterministic data pipeline
      ai_client.py     provider-agnostic LLM client + deterministic fallback
  training/train_vision.py
frontend/
  src/pages/           Dashboard · Inspection · Forensic · Propagation · Production
                       · WhatIf · Investigator · Review · Datasets
  src/components/      Layout · PropagationGraph (React Flow) · Timeline · charts · ui
scripts/
  inspect_datasets.py  reproduces every figure in DATASET_SCHEMA.md
  smoke_test_api.py    142-assertion end-to-end check of the live API
```

## 4. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS 4 | fast dev loop, utility styling for a dense control-room UI |
| Charts | Recharts | utilisation/WIP/timeline comparisons without a heavyweight chart runtime |
| Graph | `@xyflow/react` (React Flow) | the propagation graph is interactive and node-click driven |
| API | FastAPI + pydantic v2 | typed contracts end-to-end; `/docs` for free |
| Data | pandas · NumPy · SciPy · pyarrow | profiling and the utilisation-identity calibration |
| ML | scikit-learn (anomaly scoring, surrogates) · PyTorch (vision CNN) | only what the data supports — no borrowed models |
| Simulation | hand-written discrete-event engine | keeps the documented queueing semantics explicit and auditable |
| Storage | SQLite via SQLAlchemy 2.0 | zero-setup dev; the schema is PostgreSQL-compatible (`DATABASE_URL`) |
| AI | `httpx` against any OpenAI-compatible endpoint | one client, no vendor lock-in, no per-vendor SDK |

## 5. AI API integration

**The LLM does not compute anything.** All numbers — utilisation, queue means, robust-z, Cohen's d,
bottleneck scores, simulation results, economic lines — are produced by the Python backend. The
model receives a *compact, structured evidence object* and only phrases it.

```
backend computes evidence  ──►  LLM receives JSON evidence  ──►  response validated
   (numbers, statistics)         (never the raw datasets)         (pydantic, AIFinding)
                                                                        │
                                          deterministic template  ◄─────┴──►  UI
                                          engine if no key / on failure
```

Set the key in `.env` (see `.env.example` — it is the only place a key belongs):

```bash
AI_API_KEY=sk-...
AI_PROVIDER=openai
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
```

* **Provider-agnostic** — OpenAI, Azure OpenAI, OpenRouter, vLLM, Ollama, LM Studio: anything
  exposing an OpenAI-compatible `/chat/completions`.
* **Structured output is validated**, not trusted. `AIFinding` enforces the required shape
  (`finding`, `evidence[]`, `confidence`, `limitations[]`, `recommendation`) with length limits and
  a sanitiser that neutralises instruction-looking text. Model output is never executed and never
  becomes a database command.
* **Cost-controlled** — `AI_MAX_CALLS_PER_HOUR` rate limit, bounded output tokens, low temperature,
  and a response cache.
* **Graceful degradation** — with no key, or on any failure, a deterministic template engine writes
  the finding from the *same* evidence and labels itself `source: "deterministic"`. The UI always
  shows which engine answered. **Every feature works without an API key.**
* **Observable** — `/api/ai/logs` lists every attempt with usage; `/api/ai/status` explains why the
  LLM is or is not enabled.

## 6. How to run

Requires **Python 3.11+** and **Node 18+**.

```bash
# 1. dependencies
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. configuration (optional — everything runs with no .env at all)
cp .env.example .env        # then put your AI_API_KEY in it if you have one

# 3. train the vision model (optional; ~12 epochs on CPU, the Inspection page
#    says so plainly until you do)
python backend/training/train_vision.py --epochs 12

# 4. run the API (from backend/)
cd backend && python -m uvicorn app.main:app --reload --port 8001

# 5. run the UI (separate terminal)
cd frontend && npm run dev        # http://localhost:5173, proxies /api to 8001
```

Open **http://localhost:5173**. Interactive API docs: **http://localhost:8001/docs**.
The Vite dev server proxies `/api` to `http://127.0.0.1:8001`; override with
`VITE_BACKEND` if you run the API elsewhere.

### Single-origin production build

```bash
cd frontend && npm run build      # emits frontend/dist
cd backend && python -m uvicorn app.main:app --port 8001
# FastAPI serves the built SPA at http://localhost:8001
```

### Verifying the build

```bash
python -m pytest tests                                          # 100 tests
cd frontend && npm run typecheck && npm run build                # tsc, then bundle
python scripts/smoke_test_api.py --base http://127.0.0.1:8001     # 151 assertions
```

`scripts/smoke_test_api.py` exercises every endpoint against a live server — **151 assertions**
covering response shapes, gating behaviour, error paths and the "refuse to fabricate" cases.

`tests/` holds **100 tests**:

* `test_honesty_contract.py` (27) — asserts the system *refuses* to claim what the data cannot
  support: economics produces no total without a rate card, unsupported scenarios are rejected, the
  AI contract is enforced and its output neutralised as an instruction, and every finding carries
  its limitations and a causal caveat.
* `test_external_images.py` (24) — the external test-image path: separation from training, upload
  validation, the 10 MB guard, the prediction contract, and the OOD guard.
* `test_ai_fallback.py` (14) — deterministic fallback, truncation retry, rate-limit retry, text
  normalisation, injection redaction, grounding.
* `test_simulation_engine.py` (10) — the engine's arrivals, queues, capacity and calibration.
* `test_upload_flow.py` (9) — dataset upload: basename sanitisation (a crafted traversal name cannot
  escape the upload directory), the 25 MB streaming cap, rejection of non-CSV and unreadable files,
  profiler detection, capability-map shape and reasons, catalog rebuild, and that a deleted upload
  disappears from the catalog.
* `test_contract_shapes.py` (9) — payload shapes the UI depends on, each of which was found broken
  live: numpy-safe JSON, status `stages`, documented vs undocumented columns, case
  `confidence_basis`, the vision localisation disclosure, the per-change `adjustable` map, and the
  anomaly driver fields the landing page reads.
* `test_imagery_concurrency.py` (4) — the archive read path under concurrent access.
* `test_api_hardening.py` (3) — CORS configuration and the 500 handler leaking no internals.

## 7. Dataset setup

Two archives belong in the project root:

```
train.zip                                                   300.9 MB
Manufacturing Data Shared Facility - Discrete-Event Simulation.zip   166.5 MB
```

The public Mendeley resource is `https://data.mendeley.com/datasets/3rw227zxt7/2`.

**Nothing needs manual extraction.** On first start the catalog:

1. reads the image archive's index **without extracting** (a ZIP central-directory scan) and serves
   images and thumbnails straight out of the archive;
2. extracts only the simulation CSVs and the MATLAB `.mat` it needs into `data/raw/`;
3. writes **parquet/JSON caches** into `data/cache/`, so subsequent starts take ~12 s instead of
   re-reading a 311 MB CSV.

Watch progress on the **Datasets** page or poll `GET /api/datasets/status`. Force a rebuild with
`POST /api/datasets/process?force=true`.

To reproduce every measurement quoted in `DATASET_SCHEMA.md`:

```bash
python scripts/inspect_datasets.py --json     # → data/cache/dataset_report.json
```

**Read `DATASET_SCHEMA.md` before changing any analytics.** It records exactly what exists in the
data and, just as importantly, what does not.

## 8. Main features

### Dashboard
Quality, defect rate, bottleneck candidate, throughput, WIP, economic status — each card either
shows a dataset-derived number or says why it cannot.

### Data (Datasets page)
What was found, what was measured rather than assumed, and what is missing — plus an
**Upload your own dataset (CSV)** control. An uploaded file is stored under
`data/user_datasets/`, profiled (columns, types, station / process / cost / batch fields) and
listed with the supplied datasets. Every dataset carries a **capability map**:
`✓ production ✓ anomaly ✗ economics — no cost, price or revenue columns were detected`. A file
that supports nothing says so, with the reason for each of the six analysed features.

### Inspection
Two tabs share one page:

* **Factory images** — image, predicted class and confidence, per-class probabilities,
  test-time-augmentation agreement, an explicit **uncertain** flag, and a **model-attention heatmap
  labelled "not ground-truth localization"**. The archive has no boxes, masks or part IDs, so none
  are presented.
* **Test with a new image** — upload an image the model has never seen, or generate synthetic
  robustness probes, then run the same pipeline. See §8.1.

### Forensic Investigation
Pick a case (a group contrast, e.g. the lowest-output 1% of replications, or a single replication).
See the **divergence profile along the documented route**, the earliest divergence, whether other
stations moved with it, the evidence chain, the confidence basis, and the limitations.

### Failure Propagation
An interactive graph. Click a node to see the data behind it — for a station: capacity and where
that capacity came from, utilisation and whether it was exported or recomputed, queue mean/max,
headroom, rank and score components. Observed edges are solid; **declared assumptions are dashed
and stored with author and timestamp**.

### Production
Per-station cycle/queue time, WIP, utilisation, headroom and capacity, plus an explicit bottleneck
ranking and the calibration check that ties it back to the export.

### What-If
Choose a scenario the data actually supports (capacity change, processing-time change, demand
change, SKU mix), compare **current vs simulated**, and read the validation residuals. Scenarios
naming a station the engine cannot adjust are **rejected with an explanation** rather than quietly
returning a run identical to the baseline.

### Engineer Review
Every finding can be **confirmed / rejected / flagged needs review**, stored with timestamp,
decision and optional note, plus a history view and a summary. The UI states plainly that **no
automatic retraining happens** — feedback is stored for audit and future supervised retraining.

### AI Investigator
Ask a question in natural language. The backend assembles the evidence, the LLM phrases it, and the
response is validated. The full evidence object the answer was grounded on is displayed beside it.

### Datasets
Provenance, schema, per-column profiling, data-quality issues and the cross-dataset linkage report.

### 8.1 External test images — "Test with a new image"

A second, deliberately separated store for images that are **not** training data:

```text
train.zip                 training source, read-only (12,000 labelled images)
data/external_test/       TEST ONLY - never read by the training pipeline
```

* **Upload** your own PNG/JPEG (validated by magic bytes, re-encoded to PNG, stored under a
  server-generated name, 10 MB cap enforced while the body is still streaming) or **generate**
  synthetic probes with the built-in generator:
  * `variation` — real archive images with brightness, rotation, flip, blur, noise, contrast or
    resize changes (robustness probes),
  * `nonfactory` — synthetic faces, cars, trees, phones and random noise (deliberately outside the
    training distribution),
  * `mixed` — both.
* Every response and every screen carries the same statement: **External Test Image — Not Used for
  Training**, and each stored entry records `training_data: false`.
* The result panel shows prediction, confidence, TTA agreement, known/uncertain verdict and the
  attention map — plus an **out-of-distribution warning** when the image sits far from the training
  images in the model's feature space. That guard exists because the external campaign measured the
  plain CNN answering "crack" at 100 % confidence on a synthetic face; the warning is what stops a
  confidently-wrong answer from being read as an inspection result.
* Synthetic images are **not** real industrial data and carry no ground-truth labels. Generated
  variations never claim to represent real factory conditions; see
  `EXTERNAL_IMAGE_TEST_REPORT.md` for the measured campaign.

## 9. Limitations

These are properties of the supplied data, not bugs. The application surfaces each one in the UI.

1. **The two datasets cannot be joined.** The image archive has no product, batch, station or
   timestamp identifier; the simulation exports have no image reference. There is no shared key, so
   no data-derived edge exists between a surface defect class and a station deviation. Dashed
   edges are engineer-declared assumptions, drawn and stored as such, and an assumption naming a
   defect class or station that does not exist in the data is rejected (422) rather than drawn.
2. **There is no timeline in the manufacturing data.** `Time_Now` is the constant `24` in all
   605,620 rows of Model 3, Models 1–2 export no time column at all, our lag-1 autocorrelation of
   `c_TotalProducts` is 0.0004, and rows are independent replications. **"Earliest divergence" is
   therefore ordered by the documented process route, never by wall-clock time**, and is labelled
   that way throughout.
3. **No scrap, rework, downtime or defect quantity exists** in any export, so those effects cannot
   be measured, simulated or costed.
4. **No cost, price or currency exists** anywhere. The Arena models *configure* costing constructs
   (`V_VACost`, `Holding Cost/Hour`, …) and scrap logic, but **none of it is exported**. Economic
   impact is therefore *not calculable from the data*; the UI says so and only computes money from a
   user-supplied, clearly-labelled rate card.
5. **Defect localization is not supported.** There are no bounding boxes or masks, so the attention
   map is offered instead and explicitly captioned as model attention.
6. **No defect rate per batch or per station can be computed.** Defect labels exist only per image;
   production data has no quality outcome.
7. **Correlation is not causation.** Findings use *associated with*, *correlated with*, *supporting
   evidence* and *possible contributor*, and each carries a causal caveat.
8. **The re-simulation is a re-implementation**, not the original Arena model. It is calibrated to
   the measured volume and validated against the export's utilisation columns, and the residuals
   are shown so you can judge it. All output is advisory.
9. **The four unnamed MATLAB `Predictors` are undocumented.** They correlate strongly with
   `Press1..4_Util` (|r| ≈ 0.99) but their identity and units appear nowhere in the dataset, so they
   are reported as *undocumented model counters* and never as experimental factors.
10. **The re-simulation admits parts uniformly**, because the export contains no arrival schedule.
    Real Arena releases are bursty (blanking works in 2,000 kg batches), so this engine's queue
    lengths are systematically smaller than the exported `*_Queue` columns. Those columns are an
    Arena counter, not a time-weighted mean, so **queue length is compared against an unmodified run
    of this same engine rather than against the export**, and every comparison row states which
    baseline it used. Utilisation *is* compared against the export, because the utilisation identity
    is verified against those columns.
11. **Assembly Cell 4 does not validate, and that is a finding.** The export's utilisation implies a
    capacity of 5 against the 4 documented in `Model 3.pdf`; the re-simulation therefore saturates
    Cell 4 (≈100 % utilisation, queue ≈138 parts) while the export reports 81 %. The calibration
    card on the Dashboard states this contradiction rather than tuning the constant to hide it.

## 10. Engineering notes: the bugs the verification caught

All of these were silent — they produced plausible-looking output rather than errors — which is why
they are documented here and pinned by regression tests.

**1. The queue statistics were meaningless.** The engine admitted all ~59,000 parts in a pre-pass
before running its event loop. Every station therefore filled with the whole production volume
(queue maxima ≈ 15,000) and each queue's time-weighted clock was already at the end of the horizon
when the loop began, so the first event at `t ≈ 5 s` stepped it *backwards* and drove the
**time-weighted mean queue length negative**. Utilisation was unaffected because total service time
is conserved either way — which is exactly why the calibration check passed while the queue numbers
were nonsense. Arrivals are now scheduled as events. (`test_simulation_engine.py`)

**2. Concurrent image reads returned corrupt bytes.** `zipfile.ZipFile` shares one file handle and
reads a member with a seek followed by a read, so two threads reading different members interleave.
Requesting a page of thumbnails reproduced it reliably as `zlib.error: invalid stored block lengths`,
surfacing in the UI as one broken image. Archive reads are now serialised on a lock and thumbnail
cache writes are atomic. (`test_imagery_concurrency.py`)

A third issue was behavioural rather than numerical: the What-If table compared simulated queue
lengths against the export's `Queue` columns, producing deltas near **−100 %** that were purely a
definition mismatch rather than a scenario effect. Comparison rows now carry the baseline they used,
so the two can never be read as the same measurement.

**3. The graph could draw an edge to a node that did not exist.** Declaring a link for the defect
class `crazing` was accepted (it is not one of the archive's five labels), and the propagation graph
emitted `defect_crazing -> stage_blanking` against a node that was never created. A mistyped station
was worse: an unknown stage name silently resolved to the Assembly node, drawing a fabricated link.
The endpoint now validates both values against the datasets, and the graph skips (and counts)
assumptions whose endpoints do not exist. (`test_honesty_contract.py`)

**4. The AI answers were quietly coming from the template engine.** The configured model is
reasoning-capable, so part of its 900-token output budget went to hidden reasoning and the JSON reply
arrived truncated (`finish_reason=length`), which the client could not parse; a `429` from a free tier
discarded otherwise good answers too. The client now retries once with double the budget when a reply
is truncated, and retries transient status codes with backoff honouring `Retry-After`. Live narratives
report `source: llm` with grounded figures. (`test_ai_fallback.py`)

**5. Model typography broke label matching.** The model returned "Blanking" containing a soft hyphen,
so the name no longer matched the dataset label. Model text is now character-normalised (invisible
characters dropped, look-alike hyphens and spaces mapped to ASCII) before schema validation.

## 11. Documentation index

| Document | Read it for |
|---|---|
| `SYSTEM_EXPLANATION.md` | The beginner-friendly master explanation: what the system does, end to end, in order |
| `ALGORITHM_REFERENCE.md` | Every algorithm actually implemented, with plain-language explanations |
| `FUNCTION_REFERENCE.md` | Function-by-function reference across backend, frontend and training |
| `API_REFERENCE.md` | Every endpoint: input, processing, output, errors |
| `DATASET_EXPLANATION.md` | What each supplied file contains and what it can/cannot support |
| `DATASET_SCHEMA.md` | Column-level schema and quality report |
| `EXTERNAL_IMAGE_TEST_REPORT.md` | The measured external-image campaign, including the failures |
| `TEST_REPORT.md` | What was run, what passed, and what each layer caught |
| `SECURITY_AUDIT.md` | Input handling, secrets, CORS, uploads, AI-output safety |
| `RECOVERY_BASELINE.md` | Every failure found while restoring the app, traced to its root cause |
| `FINAL_AUDIT.md` | Feature-by-feature status with problem/cause/fix/verification |

## 12. What is deliberately not built

Live PLC/OPC-UA integration, robot or sorter control, physical machine control, live camera control
and IoT hardware. None of it is present, and none of the outputs can drive it. This is a
decision-support tool for humans.

---

*Nothing in this application has been fabricated. Where the data was silent, the interface says so.*
