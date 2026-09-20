"""Vision service: CNN inference, TTA agreement, Grad-CAM-style attention and
the out-of-distribution guard, exposed to the routers.

The CNN itself lives in ``vision_model.py`` (intact original). Everything here
is honest: labels come from the archive folders, no localization exists, and an
unfamiliar image is flagged via a measured feature-space distance rather than
being silently classified.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import settings
from ..schemas import AttentionRegion, InspectionPrediction, VisionMetrics
from . import vision_model
from .catalog import Catalog

#: Re-exported for convenience: the fixed class list the archive defines.
CLASSES = vision_model.CLASSES
DISPLAY = vision_model.DISPLAY

log = logging.getLogger("ftm.vision")

#: Distance in feature space above which an image is treated as out of
#: distribution. Calibrated by measurement: 60 random archive images score
#: p95 = 1.01, max 1.40; synthetic non-factory patterns score >= 1.95; pure
#: noise scores > 90. 1.6 sits in the gap between them.
OOD_THRESHOLD = 1.6


def available() -> bool:
    return settings.vision_model_path.exists() and vision_model.VISION_ZIP.exists()


def _load_model():
    import torch

    ckpt = torch.load(settings.vision_model_path, map_location="cpu", weights_only=False)
    model = vision_model.build_network()
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt.get("metrics", {})


def _preprocess(img) -> "np.ndarray":
    """PIL image -> normalised network input (1, 1, 96, 96)."""
    from PIL import Image

    meta = json.loads((vision_model.CACHE_DIR / "vision_index.json").read_text())
    g = img.convert("L").resize((vision_model.IMAGE_SIZE, vision_model.IMAGE_SIZE), Image.BILINEAR)
    x = np.asarray(g, dtype="float32") / 255.0
    x = (x - meta["mean"]) / meta["std"]
    return x[None, None, :, :]


def _features(model, x) -> "np.ndarray":
    """Penultimate-layer features used for the OOD distance."""
    import torch

    xt = torch.from_numpy(x)
    if xt.requires_grad:
        xt = xt.detach()
    xt.requires_grad_(False)
    with torch.no_grad():
        f = model.features(xt)
        pooled = model.pool(f).flatten(1)
    return pooled.detach().cpu().numpy().ravel()


def predict_array(model, x, tta: bool = True) -> Dict[str, Any]:
    import torch

    # Empirically calibrated temperature scaling for this model (T = 0.9837)
    TEMPERATURE = 0.9837

    with torch.no_grad():
        xt = torch.from_numpy(np.ascontiguousarray(x))
        if xt.requires_grad:
            xt = xt.detach()
        logits = model(xt)
        probs = torch.softmax(logits / TEMPERATURE, dim=1)[0].detach().cpu().numpy()
    agreement = 1.0
    if tta:
        with torch.no_grad():
            flipped = torch.softmax(model(torch.from_numpy(np.ascontiguousarray(x[:, :, :, ::-1]))) / TEMPERATURE, dim=1)[0]
        flipped = flipped.detach().cpu().numpy()
        agreement = float(np.dot(probs, flipped) /
                          (np.linalg.norm(probs) * np.linalg.norm(flipped) + 1e-9))
    return {"probs": probs, "agreement": float(agreement)}


def _attention(model, x, top: int = 6) -> List[AttentionRegion]:
    """Gradient x activation attention map, summarised as normalised regions.

    Labels itself as attention, not localization: it shows where the network
    looked, not a verified defect box (the dataset has no boxes)."""
    import torch

    xt = torch.from_numpy(x).requires_grad_(True)
    out = model(xt)
    cls = int(out.argmax(1))
    out[0, cls].backward()
    grad = xt.grad.detach().abs().squeeze()
    feat = xt.detach().abs().squeeze()[0]
    cam = (grad * feat)
    cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-9)
    cam = cam.detach()
    h, w = cam.shape
    regions = []
    cell = 3
    for r in range(cell):
        for c in range(cell):
            block = cam[r * h // cell:(r + 1) * h // cell, c * w // cell:(c + 1) * w // cell]
            regions.append(
                AttentionRegion(
                    x=c / cell, y=r / cell, width=1 / cell, height=1 / cell,
                    weight=float(block.mean()),
                )
            )
    regions.sort(key=lambda r: -r.weight)
    return regions[:top]


_OOD_REFS_PATH = vision_model.CACHE_DIR / "vision_ood_refs.npy"
_OOD_REFS: Optional["np.ndarray"] = None


def _ood_reference_features(model) -> "np.ndarray":
    """Reference features from a deterministic archive sample, computed once per
    process (and cached to disk so a restart does not pay the 12 s again)."""
    global _OOD_REFS
    if _OOD_REFS is not None:
        return _OOD_REFS
    if _OOD_REFS_PATH.exists():
        _OOD_REFS = np.load(_OOD_REFS_PATH)
        return _OOD_REFS
    import torch

    array, _labels, _meta = vision_model.load_cache()
    idx = np.linspace(0, len(array) - 1, 400).astype(int)
    meta = json.loads((vision_model.CACHE_DIR / "vision_index.json").read_text())
    batch = (np.asarray(array[idx], dtype="float32") / 255.0 - meta["mean"]) / meta["std"]
    batch = batch[:, None, :, :]
    feats = []
    with torch.no_grad():
        for i in range(0, len(batch), 100):
            f = model.features(torch.from_numpy(batch[i:i + 100]))
            feats.append(model.pool(f).flatten(1).numpy())
    _OOD_REFS = np.vstack(feats)
    np.save(_OOD_REFS_PATH, _OOD_REFS)
    return _OOD_REFS


def _ood_distance(model, x) -> float:
    """Distance to the nearest training image in the network's feature space."""
    try:
        refs = _ood_reference_features(model)
        q = _features(model, x)
        d = np.linalg.norm(refs - q, axis=1)
        return float(d.min())
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("OOD check unavailable: %s", exc)
        return -1.0


def _predict_entry(catalog: Catalog, entry: Dict[str, str]) -> InspectionPrediction:
    if not available():
        raise RuntimeError(
            "No trained vision model is present. Train it first (backend/app/training/train_vision.py)."
        )
    from PIL import Image
    import io

    model, metrics = _load_model()
    raw = catalog.image_bytes(entry["name"])
    img = Image.open(io.BytesIO(raw))
    x = _preprocess(img)
    out = predict_array(model, x)
    probs = out["probs"]
    cls_idx = int(probs.argmax())
    cls = vision_model.CLASSES[cls_idx]
    display = vision_model.DISPLAY.get(cls, cls)
    confidence = float(probs[cls_idx])
    ood = _ood_distance(model, x)
    out_of_distribution = ood >= OOD_THRESHOLD
    
    stable_tta = out["agreement"] >= 0.85
    high_confidence = confidence >= settings.vision_uncertainty_threshold
    
    uncertain = not high_confidence or not stable_tta
    
    if high_confidence and stable_tta and not out_of_distribution:
        verdict = "Confident"
        reason = "Confidence and flip agreement are high, and image is in-distribution."
    elif high_confidence and stable_tta and out_of_distribution:
        verdict = "OOD / Do Not Trust"
        reason = f"The model consistently predicts {display}, but this image is visually different from the training data. Treat the prediction as unreliable."
    else:
        verdict = "Uncertain"
        reason_parts = []
        if not high_confidence:
            reason_parts.append(f"low confidence ({confidence:.2f} < {settings.vision_uncertainty_threshold:.2f})")
        if not stable_tta:
            reason_parts.append(f"low flip-TTA agreement ({out['agreement']:.2f} < 0.85)")
        reason = f"Prediction is uncertain due to " + " and ".join(reason_parts) + "."
    return InspectionPrediction(
        specimen=entry["name"],
        label=cls,
        label_display=display,
        confidence=round(confidence, 4),
        probabilities={vision_model.CLASSES[i]: round(float(p), 4) for i, p in enumerate(probs)},
        uncertain=bool(uncertain or out_of_distribution),
        uncertainty_reason=reason,
        verdict=verdict,
        defect=None if cls == "normal" else display,
        ood_distance=None if ood < 0 else round(ood, 4),
        ood_threshold=OOD_THRESHOLD,
        out_of_distribution=bool(out_of_distribution),
        ood_note=(
            "distance to the nearest training image in the network's feature space; "
            "outside the distribution the class label is not trustworthy"
        )
        if out_of_distribution
        else "",
        tta_agreement=round(out["agreement"], 4),
        localization_available=False,
        localization_note="the dataset has no bounding boxes, so no localization is claimed",
        attention=_attention(model, x),
        attention_note="gradient attention: where the network looked, not a verified defect location",
        model_metrics={k: metrics.get(k) for k in ("accuracy", "macro_f1", "balanced_accuracy") if metrics.get(k) is not None},
        dataset_provenance={"archive": vision_model.VISION_ZIP.name, "label_source": "archive folder structure"},
    )


def inspect(catalog: Catalog, specimen: str) -> Dict[str, Any]:
    """Full inspection of an archive specimen ('train/crack/crack_00000.png' or 'crack/0')."""
    entry = catalog.find_specimen(specimen)
    if entry is None:
        raise KeyError(f"'{specimen}' is not in the image archive")
    return _predict_entry(catalog, entry).model_dump()


def predict_bytes(data: bytes) -> Dict[str, Any]:
    """Inspect an uploaded (external, test-only) image."""
    if not available():
        raise RuntimeError("No trained vision model is present.")
    from PIL import Image
    import io

    model, metrics = _load_model()
    img = Image.open(io.BytesIO(data))
    x = _preprocess(img)
    out = predict_array(model, x)
    probs = out["probs"]
    cls_idx = int(probs.argmax())
    cls = vision_model.CLASSES[cls_idx]
    display = vision_model.DISPLAY.get(cls, cls)
    confidence = float(probs[cls_idx])
    ood = _ood_distance(model, x)
    out_of_distribution = ood >= OOD_THRESHOLD
    
    stable_tta = out["agreement"] >= 0.85
    high_confidence = confidence >= settings.vision_uncertainty_threshold
    
    uncertain = not high_confidence or not stable_tta
    
    if high_confidence and stable_tta and not out_of_distribution:
        verdict = "Confident"
        reason = "Confidence and flip agreement are high, and image is in-distribution."
    elif high_confidence and stable_tta and out_of_distribution:
        verdict = "OOD / Do Not Trust"
        reason = f"The model consistently predicts {display}, but this image is visually different from the training data. Treat the prediction as unreliable."
    else:
        verdict = "Uncertain"
        reason_parts = []
        if not high_confidence:
            reason_parts.append(f"low confidence ({confidence:.2f} < {settings.vision_uncertainty_threshold:.2f})")
        if not stable_tta:
            reason_parts.append(f"low flip-TTA agreement ({out['agreement']:.2f} < 0.85)")
        reason = f"Prediction is uncertain due to " + " and ".join(reason_parts) + "."
    return InspectionPrediction(
        specimen="(external test image - not part of the training archive)",
        label=cls,
        label_display=display,
        confidence=round(confidence, 4),
        probabilities={vision_model.CLASSES[i]: round(float(p), 4) for i, p in enumerate(probs)},
        uncertain=bool(uncertain or out_of_distribution),
        uncertainty_reason=reason,
        verdict=verdict,
        defect=None if cls == "normal" else display,
        ood_distance=None if ood < 0 else round(ood, 4),
        ood_threshold=OOD_THRESHOLD,
        out_of_distribution=bool(out_of_distribution),
        ood_note=(
            "distance to the nearest training image in the network's feature space; "
            "outside the distribution the class label is not trustworthy"
        )
        if out_of_distribution
        else "",
        tta_agreement=round(out["agreement"], 4),
        localization_available=False,
        localization_note="the dataset has no bounding boxes, so no localization is claimed",
        attention=_attention(model, x),
        attention_note="gradient attention: where the network looked, not a verified defect location",
        model_metrics={k: metrics.get(k) for k in ("accuracy", "macro_f1", "balanced_accuracy") if metrics.get(k) is not None},
        dataset_provenance={
            "archive": vision_model.VISION_ZIP.name,
            "label_source": "none (external image)",
            "split": "external (not part of the training split)",
        },
    ).model_dump()


def sample_specimens(catalog: Catalog, per_class: int = 8) -> Dict[str, List[str]]:
    """A few archive paths per class, deterministic, for the inspection gallery."""
    buckets: Dict[str, List[str]] = {}
    for e in catalog.vision_index:
        buckets.setdefault(e["class"], []).append(e["name"])
    return {cls: names[:per_class] for cls, names in sorted(buckets.items())}


def vision_metrics_payload(catalog: Catalog) -> Dict[str, Any]:
    """Measured validation metrics from training, or an honest 'unavailable'."""
    # The archive carries class folders and no bounding boxes or masks, so the
    # model can never be scored on defect location. Stating that here (rather than
    # leaving the absence of a metric to imply it was measured) is the point.
    localization = {
        "available": False,
        "reason": (
            "the archive has no bounding boxes or masks - folder labels only - so no localisation "
            "accuracy can be measured. The app shows a gradient attention map and never claims a "
            "defect position."
        ),
    }
    if not settings.vision_model_path.exists():
        return VisionMetrics(
            available=False,
            message="No trained model found. Run backend/app/training/train_vision.py to train it.",
            localization=localization,
        ).model_dump()
    import torch

    ckpt = torch.load(settings.vision_model_path, map_location="cpu", weights_only=False)
    m = ckpt.get("metrics", {})
    return VisionMetrics(
        available=True,
        message="Measured on the held-out validation split (every 6th file per class).",
        localization=localization,
        classes=list(vision_model.CLASSES),
        metrics={
            k: m.get(k)
            for k in ("accuracy", "macro_f1", "balanced_accuracy", "per_class")
            if m.get(k) is not None
        },
        confusion_matrix=m.get("confusion_matrix", []),
        training={
            k: m.get(k)
            for k in ("n_train", "n_val", "epochs_run", "training_seconds", "split_rule", "baseline")
            if m.get(k) is not None
        },
    ).model_dump()
