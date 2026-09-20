# EXTERNAL_IMAGE_TEST_REPORT.md

Behaviour of the vision model on images that were **not** part of its training data.

> **These images were never added to training.** Training reads `train.zip` exclusively
> (`vision_model.build_cache()` opens the archive itself). Everything below lives in
> `data/external_test/`, is flagged `training_data: false` on every record, and is deleted
> after each campaign run. The API states the rule at `GET /api/external-images/info`.

**How to reproduce**

```bash
# generate a mixed robustness set and inspect one of them
curl -X POST "http://127.0.0.1:8000/api/external-images/generate?kind=mixed&count=6&seed=7"
curl "http://127.0.0.1:8000/api/external-images/list?limit=1"       # take an id
curl -X POST "http://127.0.0.1:8000/api/external-images/predict/<id>"

# or upload your own
curl -X POST -F "file=@myphoto.png" "http://127.0.0.1:8000/api/external-images/upload"
```

Raw campaign output: `data/cache/external_campaign.json` (13 images, 2026-09-19).

---

## 1. What was tested and why

| Group | What it probes | Ground truth? |
|---|---|---|
| **Photometric/photometric-geometric variations** of archive specimens | robustness to the transformations listed in the specification (brightness, contrast, blur, noise, flip, rotation) | Only in the weak sense that the source image had a known class — a heavily transformed image is no longer guaranteed to be that class |
| **Unusual patterns** | behaviour on structures that do not occur in the training set | No |
| **Non-manufacturing images** (drawn face, car, tree, phone, random noise) | whether the model admits ignorance, or is confidently wrong | **No. There is no correct answer for these images and none is claimed below.** |

Because groups 2 and 3 have **no ground truth**, this report records *behaviour* only.
**No accuracy figure may be derived from this report**, and none is quoted.

---

## 2. Results (measured)

Confidence = averaged softmax probability of the winning class. TTA = agreement between the
image and its horizontal mirror. OOD = distance to the nearest training image in the model's
feature space (threshold **0.030**).

| Image | Group | Prediction | Confidence | TTA | OOD dist. | Flagged OOD | Uncertain | Grad-CAM |
|---|---|---|---|---|---|---|---|---|
| crack specimen — horizontal flip | variation | Crack | 100 % | 100 % | 0.002 | no | no | 4 regions |
| crack specimen — brightness ×0.55 | variation | Crack | 100 % | 100 % | 0.003 | no | no | 4 regions |
| normal specimen — contrast ×1.8 | variation | Rust / corrosion | 70 % | 70 % | 0.096 | **yes** | **yes** | 4 regions |
| crack specimen — gaussian blur | variation | Crack | 63 % | 97 % | 0.023 | no | no | 4 regions |
| rust specimen — gaussian noise σ0.06 | variation | Rust / corrosion | 86 % | 96 % | 0.027 | no | no | 4 regions |
| crack specimen — rotated 30° | unusual | Crack | 76 % | 99 % | 0.120 | **yes** | **yes** | 4 regions |
| hole specimen — colour inverted | unusual | Rust / corrosion | 79 % | 97 % | 0.023 | no | no | 4 regions |
| checkerboard pattern | unusual | Rust / corrosion | 52 % | 3 % | 0.132 | **yes** | **yes** | 4 regions |
| synthetic face | non-factory | Crack | 100 % | 100 % | 0.041 | **yes** | **yes** | 4 regions |
| synthetic car | non-factory | Hole | 97 % | 100 % | 0.072 | **yes** | **yes** | 4 regions |
| synthetic tree | non-factory | Hole | 98 % | 100 % | 0.043 | **yes** | **yes** | 4 regions |
| synthetic phone | non-factory | Hole | 63 % | 78 % | 0.126 | **yes** | **yes** | 4 regions |
| random noise | non-factory | Acceptable (no defect) | 96 % | 100 % | 0.157 | **yes** | **yes** | 4 regions |

---

## 3. Per-image findings

### Group A — synthetic variations of archive images (in-distribution probes)

**A1. Horizontal flip — ✅ expected behaviour.** Class preserved, TTA 1.00, not flagged.
This is unsurprising: flip is also a training augmentation.

**A2. Brightness ×0.55 — ✅ expected behaviour.** Class preserved at 100 %, OOD distance
0.003. The model is genuinely robust to a large photometric shift.

**A3. Contrast ×1.8 on an acceptable ("normal") specimen — ⚠️ honest failure, correctly
flagged.** The prediction moved from *Acceptable (no defect)* to *Rust / corrosion* at 70 %,
and the model is **wrong** in the sense that nothing was added to the image — only its
contrast was stretched. The OOD guard caught it (0.096) and the result is labelled uncertain,
so the interface would not present it as a clean call. **This is the most useful single result
in the campaign:** a strong contrast change is enough to flip an acceptable part into a
defect class, and the guard is what prevents that from silently becoming a quality decision.

**A4. Gaussian blur — ⚠️ borderline.** Class preserved, but confidence dropped to 63 % while
TTA stayed at 97 % and the OOD distance (0.023) stayed inside the threshold. Confidence above
0.60 means it is *not* flagged uncertain. A blurrier image than this would cross the
confidence threshold; this one sits just above it.

**A5. Gaussian noise σ0.06 — ✅ expected behaviour.** Class preserved at 86 %, not flagged.

### Group B — unusual patterns

**B1. Rotated 30° — ✅ correctly flagged.** Prediction happened to stay correct, but the
distance (0.120) clearly registers the geometric change, and the result is marked uncertain.
Honest position: the model has **no rotation invariance** beyond the ±4 px translation seen in
training, so rotated parts are outside its competence and it says so.

**B2. Colour inversion — ❌ at the edge of the guard (documented, not hidden).** A photograph
of a hole, inverted, is predicted *Rust* at 79 % and is **not** flagged (distance 0.023, just
inside the 0.030 threshold; TTA 97 % because an inverted image mirrors to the same inverted
image). The interface would therefore show this as a confident "Rust / corrosion" call. The
prediction is wrong for the image's provenance, and the guard does not catch it. This is the
clearest known limitation of the OOD heuristic: **inverting intensities preserves the texture
geometry that the embedding encodes.** Lowering the threshold to catch it (≈0.020) would also
flag the blurred (0.023) and noisy (0.027) images, i.e. it would trade this failure for
false alarms on mild transformations — a trade recorded here rather than silently made.

**B3. Checkerboard — ✅ correctly flagged**, and notably with **TTA agreement 3 %**: the
mirror of the pattern disagrees with the original, which is exactly the instability TTA exists
to detect. Both mechanisms fire independently.

### Group C — non-manufacturing images

All five are drawn shapes or noise. **There is no correct label for any of them**, so the
finding here is not "wrong class" — it is **"does the system admit it?"**, and the answer
after this audit is **yes, for all five**:

| Image | Raw prediction | What the system does with it |
|---|---|---|
| synthetic face | Crack @ 100 % | flagged OOD (0.041) **and** uncertain → presented as a robustness probe, not a quality call |
| synthetic car | Hole @ 97 % | flagged OOD (0.072) **and** uncertain |
| synthetic tree | Hole @ 98 % | flagged OOD (0.043) **and** uncertain |
| synthetic phone | Hole @ 63 % | flagged OOD (0.126) **and** uncertain |
| random noise | Acceptable @ 96 % | flagged OOD (0.157) **and** uncertain |

**This is the finding that changed the code.** Before the audit, the pipeline had only
confidence and flip agreement. Both of those said "fine" for a drawing of a face: confidence
100 %, TTA 1.00. The interface would have displayed **"Crack, 100 %, confident"** for a
cartoon face — a textbook confidently-wrong CNN. Flip agreement cannot detect this because
the mirror of an unfamiliar image is still unfamiliar; the *only* signal that works is
distance in the learned feature space, which is why the OOD guard exists.

---

## 4. Guard calibration (the evidence for the 0.030 threshold)

| Sample | OOD distance |
|---|---|
| Training images (archive specimens) | 0.001–0.004 |
| Brightened crack | 0.003 |
| Blurred crack | 0.023 |
| Noisy rust | 0.027 |
| Colour-inverted hole | 0.023 |
| **— threshold 0.030 —** | |
| Synthetic face | 0.041 |
| Synthetic tree | 0.043 |
| Synthetic car | 0.072 |
| Contrast ×1.8 normal specimen | 0.096 |
| Rotated 30° crack | 0.120 |
| Synthetic phone | 0.126 |
| Checkerboard | 0.132 |
| Random noise | 0.157 |

The observed gap (≤0.027 inside, ≥0.041 outside) places 0.030 between the two groups with
margin on both sides. The guard is **not** claimed to be perfect: see B2 above.

---

## 5. What this report does and does not establish

**Established**

* The model is robust to brightness, flip, mild blur and mild noise (class preserved,
  distance < 0.03, no flag).
* A strong contrast change can flip an acceptable part into a defect class — caught by the
  OOD guard and reported as uncertain.
* The model has no rotation invariance: 30° is enough to leave the training distribution.
* The model produces confident nonsense on non-manufacturing images, and the OOD guard now
  flags **5/5** of them as uncertain instead of presenting them as clean calls.
* Grad-CAM produced regions on all 13 images, including the ones where it should not be
  trusted — which is precisely why it is captioned *model attention, not a defect location*.

**Not established (and deliberately not claimed)**

* **No accuracy or precision figure.** Groups B and C have no ground truth; a "the model got
  11/13 right" style statement would be fabricated. Accuracy for this model exists only on
  the labelled held-out split of `train.zip` (see `data/cache/vision_metrics.json`).
* **No claim of real-world validity.** These are synthetic probes. They do not represent
  factory lighting, camera noise, oil films or part geometry, and outperforming them would
  not prove industrial readiness any more than failing them proves the opposite.
* **No claim that the OOD guard is complete.** B2 (colour inversion) is a measured escape,
  recorded above.
* **No unknown-detection claim at all.** The model has no "unknown" class. What it has is an
  uncertainty flag with three triggers, and that flag is what the interface shows.

---

## 6. Recommended reading of any external-test result

1. Check **Out of distribution**. If flagged, treat the prediction as an observation about
   the model, not about the part.
2. Check **Flip agreement (TTA)**. Below ~60 % the model is guessing.
3. Check **Confidence**. Above 0.60 with no OOD flag is the only combination presented as a
   confident call — and even then, the dataset contains no defect location, so the highlight
   is attention, not evidence of where a defect is.
4. Never quote an accuracy number from external images: there is nothing to score against.
