"""Train the surface-defect classifier on train.zip.

Usage (from the project root):

    python backend/training/train_vision.py                 # ~8 epochs, CPU
    python backend/training/train_vision.py --epochs 12 --max-minutes 40
    python backend/training/train_vision.py --force-cache   # re-decode the archive

The archive is decoded once into data/cache/vision_images_96.npy (uint8,
~110 MB) and never extracted, so the repository stays clean. The measured
metrics are written to data/cache/vision_metrics.json and the weights to
data/cache/vision_cnn.pt; the API reads those files and will refuse to predict if
they are absent rather than inventing output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import ensure_dirs, settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.services import vision_model  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the Factory Time Machine surface-defect classifier")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=96)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--max-minutes", type=float, default=25.0)
    parser.add_argument("--force-cache", action="store_true")
    args = parser.parse_args()

    setup_logging()
    ensure_dirs()
    meta = vision_model.build_cache(force=args.force_cache)
    print(
        f"image cache: {meta['count']} images at {meta['size']}x{meta['size']} "
        f"({meta['class_counts']}), mean {meta['mean']:.4f}, std {meta['std']:.4f}"
    )

    metrics = vision_model.train(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        seed=args.seed,
        max_minutes=args.max_minutes,
    )

    settings.vision_metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print("\n=== measured validation performance (held-out split) ===")
    print(f"accuracy          : {metrics['accuracy']:.4f}")
    print(f"macro F1          : {metrics['macro_f1']:.4f}")
    print(f"balanced accuracy : {metrics['balanced_accuracy']:.4f}")
    for cls, m in metrics["per_class"].items():
        print(f"  {cls:8s} precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}  n={m['support']}")
    baseline = metrics.get("baseline") or {}
    if baseline.get("available"):
        print(f"classical baseline ({baseline['model']}): accuracy {baseline['accuracy']:.4f}")
    print(f"\nweights : {settings.vision_model_path}")
    print(f"metrics : {settings.vision_metrics_path}")


if __name__ == "__main__":
    main()
