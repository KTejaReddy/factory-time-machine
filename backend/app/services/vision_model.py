"""Vision model definition, cache builder and training loop.

Dataset facts this module is written against (measured, see DATASET_SCHEMA.md):

* ``train.zip`` holds 12,000 PNGs, 2,400 per class, in ``train/<class>/<file>.png``.
* Images are 256x256 stored RGB but R == G == B, i.e. effectively grayscale.
* There are **no** annotation files, so this is a pure classification task. No
  localization head is trained and no bounding box is ever claimed; the
  inspection view shows a gradient attention map and labels it as such.

Images are never extracted to disk: they are decoded straight out of the archive
into a uint8 cache array, which keeps the repository clean and the training run
reproducible.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import CACHE_DIR, VISION_ZIP

log = logging.getLogger("ftm.vision.model")

#: Class order is fixed (alphabetical, as the archive is laid out) so that label
#: indices are stable across training, evaluation and inference.
CLASSES: Tuple[str, ...] = ("crack", "hole", "normal", "rust", "scratch")

DISPLAY = {
    "crack": "Crack",
    "hole": "Hole",
    "normal": "Acceptable (no defect)",
    "rust": "Rust / corrosion",
    "scratch": "Scratch",
}

#: Defect classes are everything except ``normal``.
DEFECT_CLASSES: Tuple[str, ...] = tuple(c for c in CLASSES if c != "normal")

IMAGE_SIZE = 96
CACHE_ARRAY = CACHE_DIR / "vision_images_96.npy"
CACHE_INDEX = CACHE_DIR / "vision_index.json"

#: Mean/std of the archive in [0,1] units, computed by ``build_cache``.
DEFAULT_NORM = {"mean": 0.4, "std": 0.25}


def split_of(name: str) -> str:
    """Deterministic stratified split.

    Every 6th file of each class becomes validation data (~16.7%), the rest is
    training data. Deterministic so the reported metrics can be reproduced.
    """
    stem = Path(name).stem
    try:
        index = int(stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        index = abs(hash(stem)) % 6
    return "val" if index % 6 == 0 else "train"


def build_cache(force: bool = False, size: int = IMAGE_SIZE) -> Dict[str, Any]:
    """Decode every image in the archive once into a uint8 memmap-able array."""
    if CACHE_ARRAY.exists() and CACHE_INDEX.exists() and not force:
        meta = json.loads(CACHE_INDEX.read_text(encoding="utf-8"))
        if meta.get("size") == size and meta.get("zip_fingerprint") == _zip_fingerprint():
            return meta

    from PIL import Image

    log.info("building vision image cache from %s", VISION_ZIP.name)
    started = time.perf_counter()
    zf = zipfile.ZipFile(VISION_ZIP)
    entries: List[Dict[str, Any]] = []
    for info in sorted(zf.infolist(), key=lambda i: i.filename):
        parts = info.filename.split("/")
        if len(parts) == 3 and parts[2].lower().endswith(".png"):
            entries.append({"name": info.filename, "class": parts[1]})

    array = np.zeros((len(entries), size, size), dtype=np.uint8)
    labels = np.zeros(len(entries), dtype=np.int64)
    for i, entry in enumerate(entries):
        with zf.open(entry["name"]) as fh:
            img = Image.open(io.BytesIO(fh.read())).convert("L").resize((size, size), Image.BILINEAR)
        array[i] = np.asarray(img, dtype=np.uint8)
        labels[i] = CLASSES.index(entry["class"])

    np.save(CACHE_ARRAY, array)
    unit = array.astype("float32") / 255.0
    meta = {
        "size": size,
        "count": int(len(entries)),
        "classes": list(CLASSES),
        "class_counts": {c: int((labels == CLASSES.index(c)).sum()) for c in CLASSES},
        "mean": float(unit.mean()),
        "std": float(unit.std()),
        "zip_fingerprint": _zip_fingerprint(),
        "seconds": round(time.perf_counter() - started, 1),
        "entries": entries,
    }
    CACHE_INDEX.write_text(json.dumps({k: v for k, v in meta.items() if k != "entries"}, indent=2), encoding="utf-8")
    (CACHE_DIR / "vision_entries.json").write_text(json.dumps(entries), encoding="utf-8")
    log.info("vision cache built: %d images in %.1fs", len(entries), meta["seconds"])
    return meta


def _zip_fingerprint() -> str:
    stat = VISION_ZIP.stat()
    return hashlib.sha1(f"{stat.st_size}:{int(stat.st_mtime)}".encode()).hexdigest()[:16]


def load_cache() -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    meta = build_cache()
    array = np.load(CACHE_ARRAY, mmap_mode="r")
    entries = json.loads((CACHE_DIR / "vision_entries.json").read_text(encoding="utf-8"))
    labels = np.array([CLASSES.index(e["class"]) for e in entries], dtype=np.int64)
    return array, labels, meta


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def build_network(num_classes: int = len(CLASSES)):
    """A small CNN sized for 96x96 inputs and a CPU-only training run."""
    import torch.nn as nn

    class DefectCNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
                nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
                nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
                nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
                nn.Conv2d(128, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
            )
            self.last_conv = self.features[-2]
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.dropout = nn.Dropout(0.3)
            self.fc = nn.Linear(128, num_classes)

        def forward(self, x):
            f = self.features(x)
            pooled = self.pool(f).flatten(1)
            return self.fc(self.dropout(pooled))

    return DefectCNN()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def train(epochs: int = 8, batch_size: int = 96, lr: float = 2e-3, seed: int = 1337,
          max_minutes: float = 25.0, log_every: int = 20) -> Dict[str, Any]:
    """Train the classifier and return a metrics dict (no fabrication: measured)."""
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    torch.manual_seed(seed)
    np.random.seed(seed)

    array, labels, meta = load_cache()
    names = [e["name"] for e in json.loads((CACHE_DIR / "vision_entries.json").read_text(encoding="utf-8"))]
    split = np.array([split_of(n) for n in names])
    train_idx = np.where(split == "train")[0]
    val_idx = np.where(split == "val")[0]

    mean, std = meta["mean"], meta["std"]
    x_all = (np.asarray(array, dtype="float32") / 255.0 - mean) / std
    x_all = x_all[:, None, :, :]

    model = build_network()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.05)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    train_ds = TensorDataset(torch.from_numpy(x_all[train_idx]), torch.from_numpy(labels[train_idx]))
    val_x = torch.from_numpy(x_all[val_idx])
    val_y = torch.from_numpy(labels[val_idx])
    loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0, drop_last=True)

    history: List[Dict[str, Any]] = []
    best_acc, best_state = 0.0, None
    started = time.perf_counter()

    for epoch in range(1, epochs + 1):
        model.train()
        running, seen = 0.0, 0
        epoch_started = time.perf_counter()
        for step, (xb, yb) in enumerate(loader, start=1):
            # simple augmentation: random horizontal flip + small translation
            if np.random.rand() < 0.5:
                xb = torch.flip(xb, dims=[3])
            shift = int(np.random.randint(-4, 5))
            if shift:
                xb = torch.roll(xb, shifts=shift, dims=2)
            optimizer.zero_grad()
            out = model(xb)
            loss = criterion(out, yb)
            loss.backward()
            optimizer.step()
            running += float(loss) * len(xb)
            seen += len(xb)
            if step % log_every == 0:
                log.info("epoch %d step %d/%d loss %.4f", epoch, step, len(loader), running / max(seen, 1))
            if (time.perf_counter() - started) / 60 > max_minutes:
                log.warning("training time budget reached; stopping early at epoch %d", epoch)
                break
        scheduler.step()

        model.eval()
        with torch.no_grad():
            logits = model(val_x)
            pred = logits.argmax(1)
            acc = float((pred == val_y).float().mean())
        history.append(
            {
                "epoch": epoch,
                "train_loss": running / max(seen, 1),
                "val_accuracy": acc,
                "seconds": round(time.perf_counter() - epoch_started, 1),
            }
        )
        log.info("epoch %d: val accuracy %.4f", epoch, acc)
        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    metrics = evaluate(model, val_idx, x_all, labels)
    metrics.update(
        {
            "classes": list(CLASSES),
            "image_size": IMAGE_SIZE,
            "n_train": int(train_idx.size),
            "n_val": int(val_idx.size),
            "epochs_run": len(history),
            "epochs_requested": epochs,
            "batch_size": batch_size,
            "learning_rate": lr,
            "seed": seed,
            "normalisation": {"mean": mean, "std": std},
            "history": history,
            "training_seconds": round(time.perf_counter() - started, 1),
            "split_rule": "every 6th file of each class is validation data (deterministic, stratified by file index)",
            "class_counts": meta["class_counts"],
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "torch_version": torch.__version__,
        }
    )
    metrics["baseline"] = classical_baseline(x_all, labels, train_idx, val_idx)

    torch.save({"state_dict": model.state_dict(), "metrics": metrics}, _model_path())
    log.info("saved model + metrics to %s (val accuracy %.4f)", _model_path(), best_acc)
    return metrics


def _model_path() -> Path:
    from ..config import settings

    return settings.vision_model_path


def evaluate(model, val_idx: np.ndarray, x_all: np.ndarray, labels: np.ndarray) -> Dict[str, Any]:
    import torch

    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(x_all[val_idx]))
        pred = logits.argmax(1).numpy()
    truth = labels[val_idx]
    cm = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for t, p in zip(truth, pred):
        cm[t, p] += 1
    per_class = {}
    for i, cls in enumerate(CLASSES):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[cls] = {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "support": int(cm[i, :].sum()),
        }
    accuracy = float((pred == truth).mean())
    return {
        "accuracy": accuracy,
        "macro_f1": float(np.mean([v["f1"] for v in per_class.values()])),
        "balanced_accuracy": float(np.mean([v["recall"] for v in per_class.values()])),
        "per_class": per_class,
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_classes": list(CLASSES),
        "n_val": int(truth.size),
    }


def classical_baseline(x_all: np.ndarray, labels: np.ndarray, train_idx: np.ndarray, val_idx: np.ndarray) -> Dict[str, Any]:
    """A cheap classical baseline so the CNN's numbers have context.

    Uses HOG-style gradient histograms plus an RBF SVM, which trains in seconds on
    this subset and gives an honest lower bound for the task.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from skimage.feature import hog  # type: ignore
    except Exception:
        try:
            return _histogram_baseline(x_all, labels, train_idx, val_idx)
        except Exception as exc:  # pragma: no cover
            return {"available": False, "reason": str(exc)}
    try:
        subset = train_idx[::4]
        def feats(indices):
            return np.stack([hog(x_all[i, 0], orientations=8, pixels_per_cell=(8, 8),
                                 cells_per_block=(2, 2), feature_vector=True) for i in indices[:6000]])
        xtr, ytr = feats(subset), labels[subset]
        xva, yva = feats(val_idx), labels[val_idx]
        clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=400, n_jobs=None))
        clf.fit(xtr, ytr)
        acc = float(clf.score(xva, yva))
        return {"available": True, "model": "HOG + LogisticRegression", "accuracy": acc,
                "n_train_used": int(len(subset)), "note": "classical baseline for context only"}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _histogram_baseline(x_all: np.ndarray, labels: np.ndarray, train_idx: np.ndarray, val_idx: np.ndarray) -> Dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    def feats(indices):
        imgs = x_all[indices, 0]
        return np.concatenate([imgs.reshape(len(indices), -1),
                               (imgs > 0.5).mean(axis=(1, 2))[:, None]], axis=1)

    xtr, ytr = feats(train_idx[::4]), labels[train_idx[::4]]
    xva, yva = feats(val_idx), labels[val_idx]
    clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=300))
    clf.fit(xtr, ytr)
    return {"available": True, "model": "pixel intensity + LogisticRegression",
            "accuracy": float(clf.score(xva, yva)), "note": "classical baseline for context only"}
