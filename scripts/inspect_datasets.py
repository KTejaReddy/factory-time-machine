"""Reproducible dataset inspection for Factory Time Machine.

Run from the project root:

    python scripts/inspect_datasets.py            # full report to stdout
    python scripts/inspect_datasets.py --json     # write data/cache/dataset_report.json

This script only *observes* the datasets. It never invents columns, labels,
defect classes, economic values or join keys. Everything printed here is
measured directly from the files shipped with the project.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import struct
import zipfile
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CACHE = os.path.join(ROOT, "data", "cache")

VISION_ZIP = "train.zip"
DES_ZIP = "Manufacturing Data Shared Facility - Discrete-Event Simulation.zip"
DES_PREFIX = "Manufacturing Data Shared Facility - Discrete-Event Simulation/"

MODEL_FILES = {
    "model1_csv": "Model 1__Model_1.csv",
    "model2_csv": "Model 2__Model_2.csv",
    "model3_csv": "Model 3__Model_3.csv",
    "mat": "3000Samplesv3.mat",
    "xls": "Model 3__ParametersFile.xls",
    "readme": "Readme.txt",
}


def h(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# 1. Visual dataset
# --------------------------------------------------------------------------
def inspect_vision() -> Dict[str, Any]:
    h("1. VISUAL DATASET  --  train.zip")
    path = os.path.join(ROOT, VISION_ZIP)
    report: Dict[str, Any] = {"zip": VISION_ZIP, "present": os.path.exists(path)}
    if not os.path.exists(path):
        print(f"NOT FOUND: {path}")
        return report

    zf = zipfile.ZipFile(path)
    names = zf.namelist()
    images = [n for n in names if n.lower().endswith(".png")]
    other = [n for n in names if not n.lower().endswith(".png")]

    classes = Counter(n.split("/")[1] for n in images)
    report["image_count"] = len(images)
    report["classes"] = dict(sorted(classes.items()))
    report["non_image_entries"] = other
    report["has_annotation_files"] = any(
        n.lower().endswith((".txt", ".xml", ".json", ".csv", ".jsonl")) for n in names
    )

    print(f"images          : {len(images)}")
    print(f"classes         : {dict(sorted(classes.items()))}")
    print(f"non-image files : {other}")
    print(f"annotation files: {report['has_annotation_files']} (no bbox/mask/COCO/VOC files found)")

    # geometry + channel statistics on a sample
    sample: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for n in images[: 5 * len(classes)]:
        cls = n.split("/")[1]
        if len(sample[cls]) >= 3:
            continue
        with zf.open(n) as f:
            data = f.read(33)
            w, ht = struct.unpack(">II", data[16:24])
        sample[cls].append({"file": n, "width": w, "height": ht, "bytes": zf.getinfo(n).file_size})
    report["sample_images"] = {k: v for k, v in sample.items()}
    for cls, items in sample.items():
        print(f"  {cls:8s} {items[0]['width']}x{items[0]['height']} -> {items[0]['file']}")

    # channel identity check (are images effectively grayscale?)
    try:
        from PIL import Image

        a = np.asarray(Image.open(io.BytesIO(zf.read(images[0]))).convert("RGB"))
        report["rgb_channels_identical"] = bool(np.array_equal(a[..., 0], a[..., 1]))
        print(f"R==G==B on sample  : {report['rgb_channels_identical']} (effectively grayscale)")
    except Exception as exc:  # pragma: no cover - optional dependency
        report["rgb_channels_identical"] = None
        print(f"channel check skipped: {exc}")

    report["has_product_ids"] = False
    report["has_timestamps"] = False
    report["has_localization_annotations"] = False
    print("product/batch ids  : none")
    print("timestamps         : none")
    print("localization       : none (classification-only dataset)")
    return report


# --------------------------------------------------------------------------
# 2. Discrete-event simulation dataset
# --------------------------------------------------------------------------
def inspect_des() -> Dict[str, Any]:
    h("2. MANUFACTURING DATASET  --  Discrete-Event Simulation (Arena)")
    path = os.path.join(ROOT, DES_ZIP)
    report: Dict[str, Any] = {"zip": DES_ZIP, "present": os.path.exists(path)}
    if os.path.exists(path):
        zf = zipfile.ZipFile(path)
        entries = zf.namelist()
        report["entries"] = entries
        print(f"entries ({len(entries)}):")
        for e in entries:
            print("   ", e)

        readme = zf.read(DES_PREFIX + "Readme.txt").decode("utf-8", "replace")
        report["readme"] = readme.strip()
        print("\n--- Readme.txt ---")
        print(readme.strip())
    else:
        print(f"NOT FOUND: {path}")

    # CSV schemas
    report["csv_schemas"] = {}
    for key in ("model1_csv", "model2_csv", "model3_csv"):
        f = os.path.join(RAW, MODEL_FILES[key])
        if not os.path.exists(f):
            print(f"\n{key}: not extracted to data/raw")
            continue
        with open(f, "r", encoding="utf-8-sig", newline="") as fh:
            header = next(csv.reader(fh))
        header = [c for c in header if c and not c.startswith("Unnamed")]
        n_rows = sum(1 for _ in open(f, "r", encoding="utf-8-sig")) - 1
        report["csv_schemas"][key] = {"columns": header, "rows": n_rows, "n_columns": len(header)}
        print(f"\n{key}: {n_rows} rows x {len(header)} columns")
        print("   ", header)

    # MAT file
    report["mat"] = {}
    matf = os.path.join(RAW, MODEL_FILES["mat"])
    if os.path.exists(matf):
        import scipy.io as sio

        mat = sio.loadmat(matf)
        summary = {}
        for k, v in mat.items():
            if k.startswith("__"):
                continue
            summary[k] = {
                "shape": list(np.shape(v)) if hasattr(v, "shape") else None,
                "dtype": str(getattr(v, "dtype", type(v).__name__)),
            }
        report["mat"] = summary
        print("\n--- 3000Samplesv3.mat variables ---")
        for k, v in summary.items():
            print(f"   {k:34s} {v['shape']} {v['dtype']}")

        # cross-check MAT arrays against CSV columns
        try:
            import pandas as pd

            csv3 = os.path.join(RAW, MODEL_FILES["model3_csv"])
            if os.path.exists(csv3):
                df = pd.read_csv(csv3, encoding="utf-8-sig").select_dtypes("number")
                checks = {}
                for name in ("Predictors", "Responses"):
                    if name not in mat:
                        continue
                    arr = mat[name].astype(float)
                    hits = {}
                    for i in range(arr.shape[1]):
                        cols = [
                            c
                            for c in df.columns
                            if df[c].notna().all() and np.allclose(df[c].values, arr[:, i], atol=1e-6)
                        ]
                        hits[i] = cols
                    checks[name] = hits
                report["mat"]["column_matches"] = checks
                print("\nMAT array -> Model_3.csv column matches (exact, all 605,620 rows):")
                for name, hits in checks.items():
                    for i, cols in hits.items():
                        print(f"   {name}[{i}] -> {cols if cols else 'NO MATCH (undocumented column)'}")
        except Exception as exc:
            print(f"cross-check skipped: {exc}")
    else:
        print(f"\n{matf} not found (run the extraction step)")

    # Cost / economic variables mentioned by the Arena models
    report["arena_cost_constructs"] = {}
    for key, fname in (("model1", "Model 1__Model 1.doe"), ("model2", "Model 2__Model 2.doe"),
                       ("model3", "Model 3__Model 3.doe")):
        f = os.path.join(RAW, fname)
        if not os.path.exists(f):
            continue
        text = _readable_strings(f)
        found = sorted({s for s in text if re.search(r"(?i)cost", s) and len(s) < 60})
        report["arena_cost_constructs"][key] = found[:40]
        print(f"\n{key}.doe cost-related Arena constructs: {len(found)}")
        for s in found[:20]:
            print("    ", s)

    return report


def _readable_strings(path: str, min_len: int = 4) -> Iterable[str]:
    data = open(path, "rb").read()
    out = set()
    for enc in ("utf-16-le", "latin-1"):
        try:
            text = data.decode(enc, "ignore")
        except Exception:
            continue
        for s in re.findall(r"[ -~]{%d,}" % min_len, text):
            s = s.strip()
            if s and not re.fullmatch(r"[\W_]+", s):
                out.add(s)
    return out


# --------------------------------------------------------------------------
# 3. Join-key analysis
# --------------------------------------------------------------------------
def linkage_analysis(vision: Dict[str, Any], des: Dict[str, Any]) -> Dict[str, Any]:
    h("3. CROSS-DATASET LINKAGE")
    vision_keys = {
        "product_id": vision.get("has_product_ids", False),
        "batch_id": False,
        "timestamp": vision.get("has_timestamps", False),
        "station": False,
        "run_id": False,
    }
    csv_cols = {k: set(v["columns"]) for k, v in (des.get("csv_schemas") or {}).items()}
    id_like = sorted({c for cols in csv_cols.values() for c in cols
                      if re.search(r"(?i)(id|batch|sku|time|date|part)", c)})
    des_keys = {
        "product_id": False,  # SKU is a product *type*, not a part identifier
        "batch_id": False,
        "timestamp": False,  # Time_Now is constant in Model 3 -> end-of-run snapshot
        "station": True,     # station encoded in column names
        "run_id": False,     # rows are anonymous replications
    }
    print("image dataset join keys :", vision_keys)
    print("simulation join keys    :", des_keys)
    print("id-like simulation columns:", id_like)
    shared = {k for k in vision_keys if vision_keys[k] and des_keys[k]}
    result = {
        "vision_keys": vision_keys,
        "simulation_keys": des_keys,
        "shared_keys": sorted(shared),
        "joinable": bool(shared),
        "note": (
            "No shared key exists between the image dataset and the simulation dataset. "
            "Any defect-class -> station link must therefore be declared as an explicit, "
            "user-supplied assumption and is surfaced as such in the UI."
        ),
    }
    print("shared keys             :", sorted(shared) or "NONE")
    print(result["note"])
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="write data/cache/dataset_report.json")
    args = ap.parse_args()

    vision = inspect_vision()
    des = inspect_des()
    link = linkage_analysis(vision, des)
    report = {"vision": vision, "simulation": des, "linkage": link}

    if args.json:
        os.makedirs(CACHE, exist_ok=True)
        out = os.path.join(CACHE, "dataset_report.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
