# ALGORITHM_REFERENCE.md

Every algorithm **actually implemented** in this repository, with its input, method,
output, confidence/uncertainty handling, limitations and source location.

No algorithm is listed here that is not in the code. Where a computation is arithmetic
rather than a model, it says so — a weighted formula is never called "AI".

| # | Algorithm | Where | Used for |
|---|---|---|---|
| 1 | DefectCNN (convolutional neural net) | `services/vision_model.py` | surface-defect classification |
| 2 | Test-time augmentation (horizontal flip) | `services/vision.py` | prediction stability |
| 3 | Embedding nearest-neighbour OOD distance | `services/vision.py` | "outside the training distribution" warning |
| 4 | Grad-CAM (gradient saliency, 8×8 grid) | `services/vision.py` | model attention highlight |
| 5 | Utilisation identity | `services/analytics.py::calibration_report` | validating documented plant constants |
| 6 | PCA (eigendecomposition of covariance) | `services/analytics.py::AnomalyModel.fit` | compressing process state |
| 7 | Mahalanobis distance | `services/analytics.py::AnomalyModel._score_matrix` | scoring how unusual a run is |
| 8 | Robust z-score (median/MAD) | `services/analytics.py::robust_z`, `forensics._event_from_metric` | single-run divergence |
| 9 | Cohen's d | `services/forensics.py::_event_from_metric`, `regime_contrast` | group-vs-population divergence |
| 10 | Pearson correlation + Fisher 95 % CI | `services/analytics.py::association` | association strength |
| 11 | Spearman rank correlation | `services/analytics.py::association` | monotone association |
| 12 | Partial correlation (residualisation) | `services/forensics.py::_partial_correlation` | conditional propagation edges |
| 13 | Weighted bottleneck composite | `services/bottleneck.py::rank_bottlenecks` | ranking the constraint |
| 14 | Capacity headroom arithmetic | `services/bottleneck.py::capacity_headroom` | extra load before saturation |
| 15 | Discrete-event simulation | `services/simulation.py::DesRun` | what-if analysis |
| 16 | Linear interpolation of demand response | `services/simulation.py::demand_scenario` | demand-change answers (Models 1–2) |
| 17 | Gradient-boosting surrogate + K-fold CV | `services/analytics.py::_demand_surrogate` | out-of-fold predictive quality |
| 18 | Streamed ingestion + fingerprint cache | `services/catalog.py` | start-up performance |
| 19 | Deterministic narrative templates | `services/ai_client.py::deterministic_finding` | AI-free explanations |

---

## 1. DefectCNN — the vision classifier

**Simple explanation.** The model looks for visual patterns in an image (edges, textures,
blobs) and uses those patterns to decide which of five classes the image belongs to.

```
Name:            DefectCNN (purpose-built small CNN, trained from scratch)
Purpose:         5-class surface-condition classification
Input:           one image (any size, any colour mode) → PIL converts to grayscale
Preprocessing:   resize to 96×96 (bilinear) → scale to [0,1] → normalise with the
                 archive's measured mean (0.402) and std (0.146)
Architecture:    Conv2d(1→32) → BN → ReLU → Conv2d(32→32) → BN → ReLU → MaxPool(2)
                 Conv2d(32→64) → BN → ReLU → Conv2d(64→64) → BN → ReLU → MaxPool(2)
                 Conv2d(64→128) → BN → ReLU → Conv2d(128→128) → BN → ReLU
                 → AdaptiveAvgPool2d(1) → Dropout(0.3) → Linear(128→5)
                 (the layer before the final conv is exposed as `last_conv` for Grad-CAM)
Training:        12,000 archive images; every 6th per class is validation (~16.7 %);
                 AdamW, lr 2e-3, weight decay 1e-4, cosine schedule, label smoothing 0.05,
                 batch 96, deterministic seed 1337, random flip + ±4 px roll augmentation
Output:          softmax over [crack, hole, normal, rust, scratch]
Confidence:      softmax probability of the winning class (no calibration curve is fitted —
                 an over/under-confident model is not corrected, and that is stated)
Limitations:     no localization ability (no labels for it); no "unknown" class — hence the
                 separate OOD guard (algorithm 3); near-perfect measured accuracy reflects a
                 clean, visually distinct 5-class dataset, not industrial robustness
Where:           services/vision_model.py (build_network, train), training/train_vision.py
Status:          ✅ implemented; refuses to predict when no checkpoint exists
```

## 2. Test-time augmentation (TTA)

```
Purpose:     detect predictions that are unstable under a harmless transformation
Input:       the same tensor as the model
Algorithm:   predict once on the image and once on its horizontal mirror; average the two
             probability vectors; agreement = 1 − Σ|p − p_flip| / 2
Output:      averaged probabilities (used for the reported prediction) and `tta_agreement`
Uncertainty: agreement < 0.60 sets the `uncertain` flag
Limitation:  a symmetric transformation only. An unfamiliar image stays unfamiliar when
             mirrored, so TTA cannot detect out-of-distribution inputs — that is what
             algorithm 3 is for.
Where:       services/vision.py::_inspect_bytes
```

## 3. Embedding nearest-neighbour OOD distance

```
Purpose:     warn when the model is operating outside what it was trained on
Input:       the same preprocessed tensor
Algorithm:   build a fixed bank of 400 training-image embeddings (every 30th archive image)
             by running the convolutional trunk and global average pooling (128-d), then
             L2-normalise. For an incoming image, compute its embedding and take
             1 − max cosine similarity to the bank = distance to the nearest training image.
Output:      `ood_distance`, `ood_threshold` (0.030), `out_of_distribution` (bool)
Calibration: measured in-distribution ≤ 0.029 (archive specimens, flips, brightness ±45 %,
             blur, mild noise); out-of-distribution ≥ 0.040 (rotated 30°, non-factory shapes,
             noise, checkerboard). The threshold sits in the gap.
Why this exists: the external-image campaign showed the model calling a drawing of a face
             "crack at 100 %". Flip agreement stayed at 1.00, because the mirror of an
             unfamiliar image is still unfamiliar. Distance in the learned feature space does
             separate the two groups, so it is the guard that is used.
Limitation:  it is a heuristic, not a proof of novelty. It is also deliberately conservative:
             a moderate photometric change can measure just inside the threshold (contrast
             ×1.8 measured 0.096 and was flagged; colour inversion measured 0.023 and was
             not). It reduces confidently-wrong output; it does not eliminate it, and the UI
             says so.
Where:       services/vision.py::_embedding_bank, _embedding_distance_to_training
```

## 4. Grad-CAM (gradient-weighted attention, binned)

```
Purpose:     show which regions of the image drove the prediction
Input:       the preprocessed tensor and the winning class index
Algorithm:   forward hook on the last convolution block captures its feature map; backward
             from the winning logit captures gradients; channel-wise mean of the gradients
             weights the feature map; ReLU; normalise by the max; average into an 8×8 grid;
             return the 4 highest-weight cells
Output:      up to 4 regions with normalised x/y/width/height and a weight in [0,1]
Confidence:  not a probability — a visual explanation only
Limitation:  attention is model behaviour, NOT a defect location. The dataset has no bounding
             boxes or masks, so this is captioned accordingly everywhere it appears.
Where:       services/vision.py::_attention_regions
```

## 5. Utilisation identity (calibration check)

```
Purpose:     prove the documented plant description reproduces the exported data
Input:       the documented capacity and processing time per station; the exported counters
Algorithm:   utilisation = units_processed × processing_time / (capacity × horizon)
             compared column-by-column against the exported *_Util means; the identity is
             also inverted to capacity = work / (utilisation × horizon)
Output:      per-station observed vs implied utilisation, residual %, and an implied capacity
             with a consistency verdict at a ±10 % tolerance
Finding:     it holds for press1-4, cell1-3, cells-pool and quality. For Assembly Cell 4 the
             export implies ~5 parallel resources against the documented 4 — reported as a
             dataset contradiction, never tuned away.
Limitation:  only verifiable where counters are exported (Blanking, Paint, Quality, Forklift
             have no processed-quantity columns and are reported as "not verifiable").
Where:       services/analytics.py::calibration_report
```

## 6. PCA (principal component analysis)

```
Purpose:     compress many correlated process measurements into a few "directions of
             normal variation"
Input:       standardised process-state features (utilisation, queues, counter columns
             in log1p space)
Algorithm:   eigendecomposition (np.linalg.eigh) of the covariance matrix of the standardised
             features; keep the top ≤6 components with non-negligible eigenvalues; explained
             variance = eigenvalue / total
Plain words: it finds the axes along which normal runs actually move, so that "unusual" can
             be measured in the space normal runs occupy instead of raw coordinates.
Output:      component vectors, explained variance, per-feature loadings (importance proxy)
Where:       services/analytics.py::AnomalyModel.fit
```

## 7. Mahalanobis distance (in PCA score space)

```
Purpose:     score how unusual a simulated run is
Input:       a run's standardised feature vector
Algorithm:   project onto the retained components and scale each by sqrt(explained variance):
             score = || proj / sqrt(λ) ||₂
Output:      one score per run; threshold = the 99.5th percentile of the fitted sample
             (~0.5 % of runs flagged); per-run `drivers` = the 5 features with the largest
             standardised deviation, so the flag is explainable
Limitation:  fitted on a deterministic sample (60,000 of 605,620 rows); "anomalous" means
             "unusual process state", never "defective run" — no export records a defect.
Where:       services/analytics.py::AnomalyModel._score_matrix, anomaly_report
```

## 8. Robust z-score (median / MAD)

```
Purpose:     measure how far one value sits from the typical value without letting outliers
             define the scale
Algorithm:   z = (x − median) / (1.4826 × MAD), with a std fallback when MAD = 0
Output:      a signed magnitude; |z| ≥ 2.5 (configurable DIVERGENCE_Z) counts as a divergence
Why MAD:     queue columns are heavy-tailed; mean/std would let a handful of extreme runs
             inflate the scale and hide real deviations
Where:       services/analytics.py::robust_z, services/forensics.py::_event_from_metric
             (queues are compared in log1p space)
```

## 9. Cohen's d (standardised mean difference)

```
Purpose:     measure how different a GROUP of runs is from the population
Algorithm:   d = (mean_group − mean_baseline) / pooled_standard_deviation
             robust-z/MAD remains the per-replication statistic
Output:      d per station; |d| ≥ 0.15 (configurable DIVERGENCE_EFFECT_D) counts as divergence
Significance: reported separately as d × sqrt(n) and never used as proof — with 605,620
             replications almost any difference is "significant", so effect size decides.
             Severity bands: |d| ≥ 0.8 high, ≥ 0.5 moderate, else low.
Where:       services/forensics.py::_event_from_metric, services/analytics.py::regime_contrast
```

## 10. Pearson correlation with Fisher 95 % confidence interval

```
Purpose:     quantify linear association and how precisely it is estimated
Algorithm:   r = cov(x,y)/(σx σy); CI via Fisher z-transform (atanh, se = 1/√(n−3), tanh back)
Output:      r, CI, r², and a plain-language effect label
             (negligible < 0.10 ≤ weak < 0.25 ≤ moderate < 0.45 ≤ strong < 0.65 ≤ very strong)
Where:       services/analytics.py::association, fisher_ci, effect_label
```

## 11. Spearman rank correlation

```
Purpose:     measure monotone association when the relationship is not linear
Algorithm:   Pearson correlation of the ranks
Output:      ρ alongside r, because process relationships are monotone but clearly non-linear
Where:       services/analytics.py::association
```

## 12. Partial correlation (residualisation)

```
Purpose:     test whether an upstream station's utilisation still relates to a downstream
             station's queue once the shared load profile is removed
Input:       x = upstream utilisation, y = log1p(downstream queue), controls = press/cell/
             forklift utilisation vector
Algorithm:   regress x on the controls and y on the controls by least squares; correlate the
             residuals
Output:      a partial r; |partial| ≥ 0.05 creates a conditional-propagation edge
Limitation:  association only. Edge DIRECTION comes from the documented route, never from
             the correlation itself.
Where:       services/forensics.py::_partial_correlation, propagation_graph
```

## 13. Weighted bottleneck composite

```
Purpose:     rank the capacity-constrained station
Algorithm:   score = 0.80 × utilisation_norm + 0.12 × queue_rank + 0.08 × capacity_score
             utilisation_norm = utilisation / max measured utilisation
             queue_rank       = rank of the station's WIP evidence across stations, in [0,1]
             capacity_score   = min-max normalised 1/(1+capacity) (fewer servers = tighter)
             weights are renormalised over whichever terms exist for a station
Output:      a score in [0,1], a rank, the score components, and written evidence per
             candidate; unmeasured stations are listed separately, never scored zero
Plain words: it is a formula, not AI. Utilisation dominates because it is the direct exported
             measure of how close a station is to its limit; the other terms break near-ties.
Where:       services/bottleneck.py::rank_bottlenecks (WEIGHTS constant)
```

## 14. Capacity headroom

```
Purpose:     state how much extra load a station can take before saturation
Algorithm:   headroom = 1/utilisation − 1  (utilisation scales linearly with load because the
             documented processing times are load-independent)
Output:      a fraction per station; the plant headroom is the busiest station's value
Limitation:  arithmetic on exported utilisation and documented capacity — explicitly not a
             simulation result.
Where:       services/bottleneck.py::capacity_headroom
```

## 15. Discrete-event simulation (the what-if engine)

```
Simple explanation:  the computer creates a virtual production line, sends products through
                     the stations, lets them wait when a station is busy, and measures what
                     happens.

Name:         DesRun — a pure-Python multi-server FIFO queue network (no SimPy dependency)
Input:        horizon (86,400 s), release volume, capacity overrides, time factors,
             SKU mix, and the calibrations below
Event loop:   heapq priority queue of (time, sequence, payload) events; arrivals are
             SCHEDULED as events across the horizon (a pre-pass that admitted all parts at
             once made every queue statistic meaningless — see TEST_REPORT.md); three event
             kinds: arrive, end-of-service, end-of-QC-batch
Stations:     4 presses (capacity 2 each), 4 assembly cells (capacity 8/2/2/4), 2 paint
             conveyors (modelled as pure delay), 1 batched quality check
Statistics:   time-weighted mean queue length integrated over the horizon, observed maximum,
             busy-seconds utilisation, occupancy
Service time: press NORM(5, 0.1) s; assembly by SKU NORM(25/15/23/17, 0.1) s; quality
             TRIA(50, 55, 60) s
Calibration:  release volume = measured mean assembled parts; SKU→cell routing shares =
             measured from the c_CellX__SKUY counters; QC batch size = parts ÷ QC throughput;
             paint delay back-solved from exported conveyor utilisation
Validation:   simulated utilisation is compared with the exported *_Util columns; residuals
             are returned. Seven stations land within 5 %; Cell 4 does not, because the
             export contradicts its documented capacity — the residual is shown, not hidden.
Queue vs export: exported *_Queue columns are Arena counters, not time-weighted means, so
             queue comparisons use an unmodified run of THIS engine as the baseline, and every
             row states which baseline it used.
Limitations:  admits parts uniformly (no arrival schedule is exported), so queue lengths are
             systematically smaller than Arena's; scrap, rework and downtime cannot be
             simulated at all because no export contains them.
Where:        services/simulation.py::DesRun, _simulate, validate, _comparisons
```

## 16. Demand-response interpolation

```
Purpose:     answer a demand change for Models 1-2, which have a real designed factor
Algorithm:   Demand is an integer 1-20; the measured mean throughput per level is
             interpolated linearly with np.interp; utilisation per station is interpolated
             the same way; the saturation point (first level ≥ 95 % utilisation) is reported
Output:      current vs simulated throughput and per-station utilisation, plus the source
             curve
Limitation:  values between integer levels are interpolated, not simulated; extreme levels
             have the same number of runs as central ones.
Where:       services/simulation.py::demand_scenario, services/analytics.py::demand_response
```

## 17. Gradient-boosting surrogate with K-fold cross-validation

```
Purpose:     put an out-of-sample quality number on "what if demand changes?"
Algorithm:   GradientBoostingRegressor(max_depth=3, n_estimators=200) predicting throughput
             from Demand, evaluated with 5-fold cross-validation via cross_val_predict
Output:      out-of-fold R², RMSE, MAE
Limitation:  R² is out-of-fold, so it measures generalisation to unseen runs; it says nothing
             about physical causality.
Where:       services/analytics.py::_demand_surrogate
```

## 18. Streamed ingestion with fingerprint cache

```
Purpose:     keep page loads fast and re-read sources only when they change
Algorithm:   read the CSVs with pandas (float32), drop unnamed columns, write parquet with
             zstd compression plus a JSON fingerprint of (size, mtime); on start, reuse the
             parquet when the fingerprint matches; deterministically sample rows
             (STATS_SAMPLE_ROWS = 60,000) for population statistics and model fitting
Output:      in-memory frames, a deterministic sample, per-column profiles, Parquet caches
Limitation:  full-frame statistics (population_stats) are computed once per process and
             cached; the sample is used where a full scan would be wasteful, and the API
             states which one produced a figure.
Where:       services/catalog.py
```

## 19. Deterministic narrative templates

```
Purpose:     produce a validated, evidence-grounded finding with no network call
Algorithm:   per topic (overview / case / station / scenario / question), collect labels and
             numbers ONLY from the supplied evidence bundle, assemble a fixed-sentence
             narrative, carry the evidence's own `limitations` through, and derive confidence
             from the evidence's confidence field
Output:      the same AIFinding contract the LLM must satisfy
Limitation:  it restates; it does not reason. It cannot invent a number because it has no
             source of numbers other than the evidence bundle.
Where:       services/ai_client.py::deterministic_finding
```

---

## Algorithms that are deliberately absent

Mentioned so a reviewer does not assume they exist:

| Not implemented | Why |
|---|---|
| Defect localization (object detection / segmentation) | the dataset has zero bounding boxes or masks |
| Time-series drift detection / changepoint detection | there are no timestamps — rows are independent replications |
| Retraining or recalibration from engineer feedback | feedback is stored only; claiming learning would be false |
| Cost optimisation / profitability modelling | no cost, price or currency exists in any dataset |
| Live equipment/PLC/OPC-UA control | out of scope by design; the tool is advisory-only |
