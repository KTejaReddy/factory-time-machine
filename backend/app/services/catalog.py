"""Dataset catalog.

Loads the three simulation exports (from the surviving parquet caches, rebuilt
from the raw CSVs when missing), the MATLAB surrogate design, and the NEU image
archive index; plus any CSVs the user drops into ``data/user_datasets``.

Everything the analytics need is exposed here: frames, population statistics,
samples, the vision index, provenance and the capability map. No fabricated
values: a dataset that is absent is reported as absent.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, DATA_DIR, MODEL_CSVS, RAW_DIR, ensure_dirs
from ..schemas import ColumnProfile, DatasetIssue, ProcessingStatus
from . import vision_model
from .capabilities import map_capabilities
from .profiler import profile_dataframe
from . import domain

log = logging.getLogger("ftm.catalog")

# Anchored to the project root, not the process CWD: started from ``backend/``
# (as the docs suggest) a relative path silently looked in backend/data/.
USER_UPLOAD_DIR = DATA_DIR / "user_datasets"


class StatusWrapper:
    """Wraps the ProcessingStatus with the ``.snapshot()`` accessor the routers
    and tests use, and guards against reads while the catalog builds."""

    def __init__(self, status: Optional[ProcessingStatus] = None) -> None:
        self._status = status or ProcessingStatus(state="idle", progress=0.0)

    def set(self, status: ProcessingStatus) -> None:
        self._status = status

    def snapshot(self) -> Dict[str, Any]:
        s = self._status
        return {
            "state": s.state,
            "progress": s.progress,
            "step": s.step,
            "message": s.message,
            # Per-stage timings and the started/finished stamps are part of the
            # documented payload; dropping them hid why a build was slow.
            "stages": dict(s.stages or {}),
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "seconds": s.seconds,
            "error": s.error,
        }


class Catalog:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.status = StatusWrapper(ProcessingStatus(state="idle", progress=0.0))
        self.frames: Dict[str, pd.DataFrame] = {}
        self.profiles: Dict[str, List[ColumnProfile]] = {}
        self.user_profiles: Dict[str, Dict[str, Any]] = {}
        self.issues: List[DatasetIssue] = []
        self._stats_cache: Dict[str, pd.DataFrame] = {}
        self._derived: Dict[str, Dict[str, Any]] = {}
        # Memo for expensive per-dataset analyses (the Mahalanobis anomaly report
        # is seconds of linear algebra on 605k rows and several pages ask for it).
        # Reset on every build so a new upload cannot serve a stale report.
        self.analysis_cache: Dict[Any, Any] = {}
        self._vision_index: List[Dict[str, str]] = []
        self._thumb_cache: Dict[str, bytes] = {}
        self._vision_zip = None  # lazily opened shared archive handle
        self._vision_zip_lock = threading.Lock()
        self._image_lock = threading.Lock()

    def vision_zip(self):
        """The shared, lazily opened ZipFile handle for the image archive.

        One ``Catalog`` means one handle for the process: concurrent reads are
        serialised on ``_image_lock`` inside ``image_bytes`` (ZipFile seeks are
        not thread-safe), and the double-checked lock here stops two threads
        from both opening the archive."""
        if self._vision_zip is None:
            with self._vision_zip_lock:
                if self._vision_zip is None:
                    import zipfile

                    self._vision_zip = zipfile.ZipFile(vision_model.VISION_ZIP)
        return self._vision_zip

    # ------------------------------------------------------------------ build
    def start_background_build(self, force: bool = False) -> None:
        self.build(force=force)

    def is_ready(self) -> bool:
        """True when a build has finished and none is running.

        Callers that read the loaded frames outside a request handler need this:
        a half-built catalog has some datasets loaded and others not, which would
        otherwise be reported as "dataset missing" rather than "still loading".
        """
        if not self._lock.acquire(blocking=False):
            return False
        try:
            return self.status.snapshot()["state"] == "ready"
        finally:
            self._lock.release()

    def build(self, force: bool = False) -> None:
        with self._lock:
            t0 = time.perf_counter()
            self.status.set(ProcessingStatus(state="running", progress=0.05, step="loading"))
            try:
                self.frames = {}
                self.profiles = {}
                self.issues = []
                self._stats_cache = {}
                self._derived = {}
                self.analysis_cache = {}
                # Must be cleared too: without this a CSV that was deleted from
                # data/user_datasets stayed in the dataset list (and kept a stale
                # capability map) for the life of the process.
                self.user_profiles = {}

                for key, path in MODEL_CSVS.items():
                    df = self._load_model(key, path, force)
                    if df is not None:
                        self.frames[key] = df
                        self.profiles[key] = self._profile_columns(key, df)
                        self.status.set(ProcessingStatus(
                            state="running", progress=0.3 + 0.2 * len(self.frames),
                            step=f"loaded {key}", message=f"{len(df):,} rows",
                        ))

                self._load_user_datasets()
                self._derive()
                self._collect_issues()
                self._load_vision_index()
                # Every dataset gets one case file (stable id, upload date, profile,
                # capabilities, saved history) - this is what makes a dataset
                # switchable instead of destructive.
                try:
                    from . import workspace

                    workspace.sync_registry(self)
                except Exception as exc:  # pragma: no cover - registry is not fatal
                    log.warning("dataset registry sync failed: %s", exc)
                self.status.set(ProcessingStatus(
                    state="ready",
                    progress=1.0,
                    step="done",
                    message=f"{len(self.frames)} datasets ready",
                    seconds=round(time.perf_counter() - t0, 2),
                ))
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("catalog build failed")
                self.status.set(ProcessingStatus(state="failed", progress=1.0, error=str(exc)))

    def _load_model(self, key: str, path: Path, force: bool) -> Optional[pd.DataFrame]:
        parquet = CACHE_DIR / f"{key}.parquet"
        meta_path = CACHE_DIR / f"{key}.cache.json"
        if not force and parquet.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if path.exists():
                stat = path.stat()
                fp = meta.get("fingerprint", {})
                if fp.get("size") == stat.st_size and fp.get("mtime") == stat.st_mtime:
                    return pd.read_parquet(parquet)
        if not path.exists():
            if parquet.exists():
                return pd.read_parquet(parquet)
            self.issues.append(
                DatasetIssue(
                    severity="warning",
                    message=f"{key}: source CSV not found ({path.name}) and no cache present",
                )
            )
            return None
        df = pd.read_csv(path)
        df.to_parquet(parquet)
        meta_path.write_text(
            json.dumps(
                {
                    "fingerprint": {"size": path.stat().st_size, "mtime": path.stat().st_mtime},
                    "rows": int(len(df)),
                    "columns": list(map(str, df.columns)),
                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                indent=2,
            )
        )
        return df

    def _load_user_datasets(self) -> None:
        if not USER_UPLOAD_DIR.exists():
            return
        for path in sorted(USER_UPLOAD_DIR.glob("*.csv")):
            try:
                df = pd.read_csv(path)
                key = f"user:{path.stem}"
                self.frames[key] = df
                self.user_profiles[key] = profile_dataframe(df)
            except Exception as exc:
                self.issues.append(
                    DatasetIssue(severity="error", message=f"user dataset {path.name}: {exc}")
                )

    def _derive(self) -> None:
        df = self.frames.get("model3")
        if df is not None:
            counter_cols = [c for c in df.columns if c.startswith("c_Cell") and "__SKU" in c]
            if counter_cols:
                self._derived["model3_parts"] = {
                    "total_parts": df[counter_cols].fillna(0.0).sum(axis=1),
                    "columns": counter_cols,
                }

    def _collect_issues(self) -> None:
        for key, df in self.frames.items():
            missing = int(df.isna().sum().sum())
            if missing:
                self.issues.append(
                    DatasetIssue(
                        severity="warning",
                        message=f"{key}: {missing:,} missing cells",
                        rows_affected=missing,
                    )
                )
            dupes = int(df.duplicated().sum())
            if dupes:
                self.issues.append(
                    DatasetIssue(
                        severity="warning",
                        message=f"{key}: {dupes:,} duplicated rows",
                        rows_affected=dupes,
                    )
                )
        self.issues.extend(
            [
                DatasetIssue(
                    severity="info",
                    message=(
                        "model3: Time_Now is constant 24 in all rows, so rows are independent replications "
                        "and no wall-clock ordering can be claimed"
                    ),
                ),
                DatasetIssue(
                    severity="warning",
                    message=(
                        "model3: exported Cell4 utilisation implies ~5 parallel resources while the "
                        "documented specification says 4 (reported, not tuned away)"
                    ),
                ),
                DatasetIssue(
                    severity="info",
                    message="no dataset contains cost, scrap, rework or downtime values",
                ),
            ]
        )

    # ------------------------------------------------------------- accessors
    def model_df(self, key: str) -> pd.DataFrame:
        if key not in self.frames:
            raise KeyError(f"dataset '{key}' not found")
        return self.frames[key]

    def sample_df(self, key: str) -> pd.DataFrame:
        from ..config import settings

        df = self.model_df(key)
        return df.head(settings.stats_sample_rows)

    def population_stats(self, key: str) -> pd.DataFrame:
        with self._lock:
            if key not in self._stats_cache:
                self._stats_cache[key] = self.model_df(key).describe().T
            return self._stats_cache[key]

    def population_quantiles(self, key: str, columns: List[str]) -> pd.DataFrame:
        return self.model_df(key)[columns].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).T

    # ------------------------------------------------------------- summaries
    def dataset_summaries(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key, spec in domain.MODELS.items():
            df = self.frames.get(key)
            out.append(
                {
                    "key": key,
                    "label": spec.label,
                    "kind": "simulation",
                    "present": df is not None,
                    "rows": 0 if df is None else int(len(df)),
                    "columns": 0 if df is None else int(df.shape[1]),
                    "source_file": MODEL_CSVS[key].name,
                    "description": spec.notes[0] if spec.notes else "",
                    "warnings": [n for n in spec.notes if "CONTRADICTION" in n],
                    "capabilities": self.capabilities(key) if df is not None else {},
                }
            )
        for key, prof in self.user_profiles.items():
            out.append(
                {
                    "key": key,
                    "label": f"Uploaded dataset: {key.split(':', 1)[1]}",
                    "kind": "simulation",
                    "present": True,
                    "rows": prof["stats"]["rows"],
                    "columns": len(prof["columns"]),
                    "source_file": key.split(':', 1)[1] + ".csv",
                    "description": "user-uploaded CSV",
                    "warnings": [],
                    "capabilities": self.capabilities(key),
                }
            )
        if vision_model.VISION_ZIP.exists():
            counts = self.vision_class_counts()
            out.append(
                {
                    "key": "images",
                    "label": "Surface defect image archive (NEU-style, 5 classes)",
                    "kind": "images",
                    "present": True,
                    "rows": sum(counts.values()),
                    "columns": len(counts),
                    "source_file": vision_model.VISION_ZIP.name,
                    "description": "12,000 PNGs in train/<class>/; labels from folders",
                    "warnings": [],
                    # Same shape as every other dataset so the UI renders one way.
                    "capabilities": {
                        "available": {
                            "vision": True,
                            "production": False,
                            "anomaly": False,
                            "forensics": False,
                            "economics": False,
                            "simulation": False,
                        },
                        "reasons": {
                            "production": "the image archive carries no process measurements; the production analyses run on the exported simulation data",
                        },
                    },
                }
            )
        return out

    def vision_class_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in self._vision_index:
            counts[e["class"]] = counts.get(e["class"], 0) + 1
        return counts

    # ------------------------------------------------------------ properties the routers expect
    @property
    def vision_index(self) -> List[Dict[str, str]]:
        return self._vision_index

    @property
    def vision_classes(self) -> Dict[str, int]:
        return self.vision_class_counts()

    # --------------------------------------------------------------- vision
    def _load_vision_index(self) -> None:
        self._vision_index = []
        try:
            if vision_model.VISION_ZIP.exists():
                vision_model.build_cache()
                entries_path = CACHE_DIR / "vision_entries.json"
                if entries_path.exists():
                    self._vision_index = json.loads(entries_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("vision index unavailable: %s", exc)

    def find_specimen(self, specimen: str) -> Optional[Dict[str, str]]:
        """Resolve 'train/crack/crack_00000.png' or 'crack/0' to an index entry."""
        if not self._vision_index:
            return None
        if "/" in specimen and not specimen.startswith("train/"):
            parts = specimen.split("/")
            if len(parts) == 2:
                cls, idx = parts
                try:
                    i = int(idx)
                except ValueError:
                    return None
                if 0 <= i < len(self._vision_index):
                    # entries are already sorted by path, so per-class position i
                    # is simply the i-th entry of that class
                    in_class = [e for e in self._vision_index if e["class"] == cls]
                    if 0 <= i < len(in_class):
                        return in_class[i]
            return None
        return next((e for e in self._vision_index if e["name"] == specimen), None)

    def image_bytes(self, name: str) -> bytes:
        zf = self.vision_zip()
        # ZipFile.read is seek+read on one shared handle: serialise it.
        with self._image_lock:
            return zf.read(name)

    def thumbnail(self, name: str, size: int = 160) -> bytes:
        key = f"{size}_{hashlib.sha1(name.encode()).hexdigest()}"
        cached = CACHE_DIR / "thumbnails" / f"{key}.png"
        if cached.exists():
            return cached.read_bytes()
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        from PIL import Image

        raw = Image.open(io.BytesIO(self.image_bytes(name))).convert("L")
        raw.thumbnail((size, size))
        buf = io.BytesIO()
        raw.save(buf, format="PNG")
        data = buf.getvalue()
        self._thumb_cache[key] = data
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    # ---------------------------------------------------------- provenance etc
    def provenance(self) -> Dict[str, Any]:
        from ..config import settings

        return {
            "sources": [
                {"name": p.name, "kind": "simulation export", "present": p.exists()}
                for p in MODEL_CSVS.values()
            ],
            "horizon": {
                "seconds": settings.sim_horizon_seconds,
                "source": "SIM_HORIZON_SECONDS setting (one documented production day)",
            },
            "documents": ["Model 3.pdf", "ParametersFile.xls", "3000Samplesv3.mat"],
            "derived_columns": ["total_parts (sum of the exported cell counters, model3)"],
        }

    def linkage_report(self) -> Dict[str, Any]:
        return {
            "joinable": False,
            "shared_keys": [],
            "vision_domain": {"images": len(self._vision_index)},
            "simulation_domain": {"models": [k for k in self.frames if not k.startswith("user:")]},
            "missing_identifiers": {
                "timestamp": (
                    "Model 3's Time_Now is constant 24 in all rows and Models 1 and 2 export no time "
                    "column at all, so the two domains cannot even be aligned in time."
                ),
                "batch": (
                    "The image archive carries no batch, lot or part identifier, and the simulation "
                    "exports none either, so there is no batch key to join on."
                ),
                "station": (
                    "Images are labelled by defect class only; the archive never records which station "
                    "produced a part, so defects cannot be attached to stations from data."
                ),
            },
            "statement": (
                "The image archive carries no part, batch or station identifier and the simulation exports "
                "carry no image reference; there is nothing to join on."
            ),
        }

    def capabilities(self, key: str = "model3") -> Dict[str, Any]:
        df = self.frames.get(key)
        if df is None:
            return {
                "available": {k: False for k in (
                    "vision", "production", "anomaly", "forensics", "economics", "simulation"
                )},
                "reasons": {"dataset": "dataset not loaded"},
            }
        if key.startswith("user:"):
            return map_capabilities(self.user_profiles.get(key, {}))
        return {
            "available": {
                "vision": True,
                "production": True,
                "anomaly": True,
                "forensics": True,
                "economics": False,
                "simulation": True,
            },
            "reasons": {
                "economics": "no export contains a cost, scrap, rework or downtime value; supply a rate card",
            },
        }

    def get_capabilities(self) -> Dict[str, Any]:
        return {"datasets": {k: self.capabilities(k) for k in self.frames}}

    def profile_map(self, key: str) -> Dict[str, Any]:
        return self.user_profiles.get(key, {})

    def mat_summary_lazy(self) -> Dict[str, Any]:
        path = RAW_DIR / "3000Samplesv3.mat"
        if not path.exists():
            return {"present": False, "reason": "MAT file not found"}
        try:
            from scipy.io import loadmat

            mat = loadmat(path)
            arrays = {k: v for k, v in mat.items() if not k.startswith("__")}
            summary = {
                "present": True,
                "file": path.name,
                "variables": {k: list(np.atleast_2d(v).shape) for k, v in arrays.items()},
            }
            return summary
        except Exception as exc:
            return {"present": False, "reason": str(exc)}

    # ------------------------------------------------------- column profiling
    def _profile_columns(self, key: str, df: pd.DataFrame) -> List[ColumnProfile]:
        from ..config import settings

        sample = df.head(settings.stats_sample_rows)
        profiles: List[ColumnProfile] = []
        documented_cols: set[str] = set()
        spec = domain.MODELS.get(key)
        if spec is not None:
            for st in spec.stations:
                for col in [st.utilization_column, *st.queue_columns, *st.counter_columns]:
                    if col:
                        documented_cols.add(col)
        for col in df.columns:
            s = sample[col]
            numeric = pd.to_numeric(s, errors="coerce")
            is_numeric = numeric.notna().sum() > 0 and pd.api.types.is_numeric_dtype(df[col])
            name = str(col)
            # ``documented`` was never actually set, so every column claimed to be
            # documented and the UI's "documented only" filter was a no-op.
            is_documented = name in documented_cols
            if is_documented:
                group = "documented"
            elif name.startswith(("c_", "v_", "Time_")):
                group = "derived"
            else:
                group = "exported"
            prof = ColumnProfile(
                name=name,
                group=group,
                documented=is_documented,
                note=(
                    ""
                    if is_documented
                    else "not referenced by any documented station or throughput definition"
                ),
                dtype=str(df[col].dtype),
                count=int(s.notna().sum()),
                missing=int(s.isna().sum()),
            )
            if is_numeric:
                prof.mean = float(numeric.mean())
                prof.std = float(numeric.std())
                prof.minimum = float(numeric.min())
                prof.maximum = float(numeric.max())
                prof.median = float(numeric.median())
                prof.p05 = float(numeric.quantile(0.05))
                prof.p95 = float(numeric.quantile(0.95))
            profiles.append(prof)
        return profiles


ensure_dirs()
catalog = Catalog()
