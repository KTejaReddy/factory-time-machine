"""Dataset workspace (case-file) layer.

Every dataset - the supplied archives and each uploaded CSV - gets one case file
with a stable id, an upload date, a profiled structure, a capability map and
whatever analysis, rate card, scenario history, feedback and AI findings have been
saved against it.

The rules this module enforces:

* a dataset's identity is stable (``ds-<slug>``) and its records are keyed by the
  catalog key, so a page can never read Dataset A's saved scenario while showing
  Dataset B;
* deleting a dataset (or losing its CSV) never silently deletes its history - the
  record stays, flagged ``file_missing``;
* nothing here computes an analysis. It stores and retrieves; the numbers come
  from the analytics services.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from ..db import (
    AnalysisRun,
    AppPreference,
    DatasetRecord,
    FeedbackItem,
    RateCardEntry,
    ScenarioRun,
    insert_row,
    list_rows,
    session,
    utcnow,
)

log = logging.getLogger("ftm.workspace")

#: Which capabilities make a dataset "fully analysable" vs "partial".
_CORE_FEATURES = ("production", "anomaly", "forensics", "simulation", "economics")

STATUS_LABELS = {
    "analysis_ready": "Analysis ready",
    "partial_analysis": "Partial analysis",
    "images_only": "Images only",
    "file_missing": "Source file missing",
    "pending_build": "Waiting for catalog rebuild",
}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "dataset"


def dataset_id(key: str) -> str:
    """Stable, URL-safe id for a catalog key (``user:factory_batch_A`` -> ``ds-factory-batch-a``)."""
    if key == "images":
        return "ds-images"
    name = key.split(":", 1)[1] if key.startswith("user:") else key
    return f"ds-{slugify(name)}"


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _naive_utc(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """SQLite hands back naive datetimes; comparisons must not mix aware and naive."""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def derive_status(present: bool, capabilities: Dict[str, Any]) -> str:
    if not present:
        return "file_missing"
    available = (capabilities or {}).get("available", {}) or {}
    if available.get("vision") and not any(available.get(f) for f in _CORE_FEATURES):
        return "images_only"
    if available.get("production") and available.get("anomaly"):
        return "analysis_ready"
    if any(available.get(f) for f in _CORE_FEATURES):
        return "partial_analysis"
    return "images_only" if available.get("vision") else "partial_analysis"


# ---------------------------------------------------------------------------
# Registry synchronisation (called by the catalog after every build)
# ---------------------------------------------------------------------------
def _record_payload(catalog: Any, key: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    present = bool(summary.get("present"))
    # A summary already carries its own capability map, and for a dataset that is
    # not a dataframe (the image archive) that map is the only correct one -
    # asking ``catalog.capabilities("images")`` returns "dataset not loaded".
    capabilities = (summary.get("capabilities") or {}) if present else {}
    if present and not capabilities:
        capabilities = catalog.capabilities(key)
    profile = catalog.profile_map(key) if key.startswith("user:") else {}
    source_file = summary.get("source_file") or ""
    uploaded_at = None
    if key.startswith("user:"):
        from .catalog import USER_UPLOAD_DIR

        candidate = USER_UPLOAD_DIR / source_file
        if candidate.exists():
            uploaded_at = dt.datetime.fromtimestamp(candidate.stat().st_mtime, tz=dt.timezone.utc)
    return {
        "key": key,
        "name": summary.get("label") or key,
        "kind": summary.get("kind") or "simulation",
        "source_file": source_file,
        "uploaded_at": uploaded_at,
        "status": derive_status(present, capabilities),
        "rows": int(summary.get("rows") or 0),
        "columns": int(summary.get("columns") or 0),
        "profile": profile,
        "capabilities": capabilities,
        "present": present,
    }


def sync_registry(catalog: Any) -> None:
    """Reconcile stored case files with what the catalog actually loaded."""
    summaries = {s["key"]: s for s in catalog.dataset_summaries()}
    with session() as db:
        existing = {row.key: row for row in db.execute(select(DatasetRecord)).scalars()}
        for key, summary in summaries.items():
            payload = _record_payload(catalog, key, summary)
            row = existing.get(key)
            if row is None:
                db.add(
                    DatasetRecord(
                        id=dataset_id(key),
                        key=key,
                        name=payload["name"],
                        kind=payload["kind"],
                        source_file=payload["source_file"],
                        uploaded_at=payload["uploaded_at"] or utcnow(),
                        status=payload["status"],
                        rows=payload["rows"],
                        columns=payload["columns"],
                        profile=json.dumps(payload["profile"], default=str),
                        capabilities=json.dumps(payload["capabilities"], default=str),
                        present=payload["present"],
                    )
                )
                continue
            row.name = payload["name"]
            row.kind = payload["kind"]
            row.source_file = payload["source_file"]
            row.status = payload["status"]
            row.rows = payload["rows"]
            row.columns = payload["columns"]
            row.profile = json.dumps(payload["profile"], default=str)
            row.capabilities = json.dumps(payload["capabilities"], default=str)
            row.present = payload["present"]
            # The upload date is the file's date: keep it stable once recorded,
            # refresh it only when the file itself is newer (a re-upload).
            if payload["uploaded_at"] and (
                row.uploaded_at is None
                or _naive_utc(payload["uploaded_at"]) > _naive_utc(row.uploaded_at)
            ):
                row.uploaded_at = payload["uploaded_at"]
            row.updated_at = utcnow()

        # A dataset whose source vanished keeps its row (history preserved) but is
        # flagged, so the switch UI can say "source file missing" instead of hiding it.
        # Only a genuinely absent file counts as gone: being missing from *this*
        # build's summaries can also mean the catalog simply did not load it (a build
        # that raced the upload, or a name it could not parse), and calling that
        # "source file missing" invented a deletion the user never performed.
        for key, row in existing.items():
            if key in summaries or not row.present:
                continue
            if uploaded_file_path(key) is not None:
                continue
            row.present = False
            row.status = "file_missing"
            row.updated_at = utcnow()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _artifact_counts(dataset_key: str) -> Dict[str, int]:
    """How many saved artifacts this dataset holds.

    Every count runs *inside* the session block. Calling the helper after the
    block closed the session and made SQLAlchemy silently open a fresh connection
    that was never returned to the pool - after a handful of page loads the pool
    (5 + 10 overflow) was exhausted and every request timed out for 30 s. The
    browser walkthrough is what surfaced it.
    """
    with session() as db:
        def count(model_: Any) -> int:
            return int(
                db.execute(
                    select(func.count()).select_from(model_).where(model_.dataset_key == dataset_key)
                ).scalar_one()
            )

        return {
            "scenarios": count(ScenarioRun),
            "feedback": count(FeedbackItem),
            "analyses": count(AnalysisRun),
            "has_rate_card": bool(
                db.execute(
                    select(func.count()).select_from(RateCardEntry).where(RateCardEntry.dataset_key == dataset_key)
                ).scalar_one()
            ),
        }


def reconcile_presence(catalog: Any = None) -> int:
    """Correct case-file presence flags that drifted from the source files.

    ``sync_registry`` only runs inside a catalog build, so a build that ran before
    an upload was in place could leave a dataset flagged ``file_missing`` for the
    life of the database. Its history stayed intact, but the switcher told the user
    their dataset was gone - the exact confusion this layer exists to prevent. The
    file on disk is the authority, so reads reconcile against it: nothing is
    recomputed, only the flag and its label.

    Returns the number of records corrected.
    """
    with session() as db:
        changed = 0
        for row in db.execute(select(DatasetRecord)).scalars():
            if not row.key.startswith("user:"):
                # The supplied archives and the image archive are governed by the
                # catalog build itself; their "file" is a ZIP or a cache, not this dir.
                continue
            on_disk = uploaded_file_path(row.key) is not None
            loaded = catalog is not None and row.key in (getattr(catalog, "frames", {}) or {})
            present = on_disk and (loaded or catalog is None)
            if on_disk and not loaded and catalog is not None:
                status = "pending_build"
            else:
                try:
                    capabilities = json.loads(row.capabilities or "{}")
                except ValueError:
                    capabilities = {}
                status = derive_status(present, capabilities)
            if present == row.present and status == row.status:
                continue
            row.present = present
            row.status = status
            row.updated_at = utcnow()
            changed += 1
        return changed


def list_records() -> List[Dict[str, Any]]:
    rows = sorted(
        (row.as_dict() for row in list_rows(DatasetRecord, limit=500, order_desc=False)),
        key=lambda r: (r["uploaded_at"] or "", r["key"]),
        reverse=True,
    )
    for record in rows:
        record["dataset_id"] = record["id"]
        record["status_label"] = STATUS_LABELS.get(record["status"], record["status"])
        record["artifacts"] = _artifact_counts(record["key"])
    return rows


def find_record(key_or_id: str) -> Optional[Dict[str, Any]]:
    if not key_or_id:
        return None
    with session() as db:
        row = db.execute(select(DatasetRecord).where(DatasetRecord.key == key_or_id)).scalar_one_or_none()
        if row is None:
            row = db.execute(select(DatasetRecord).where(DatasetRecord.id == key_or_id)).scalar_one_or_none()
    if row is None:
        return None
    record = row.as_dict()
    record["dataset_id"] = record["id"]
    record["status_label"] = STATUS_LABELS.get(record["status"], record["status"])
    record["artifacts"] = _artifact_counts(record["key"])
    return record


def context(key: str) -> Dict[str, Any]:
    """Everything the workspace header needs for one dataset, plus saved history."""
    record = find_record(key)
    analyses = [row.as_dict() for row in list_rows(AnalysisRun, limit=5)]
    analyses = [a for a in analyses if a["dataset_key"] == key]
    return {
        "dataset": record,
        "latest_analysis": analyses[0] if analyses else None,
        "analysis_runs": [
            {"id": a["id"], "created_at": a["created_at"], "status": a["status"], "seconds": a["seconds"]}
            for a in analyses
        ],
        "rate_card": load_rate_card(key),
        "scenarios": _scenario_history(key, limit=200),
        "feedback_count": _artifact_counts(key)["feedback"],
    }


def _scenario_history(key: str, limit: int = 200) -> List[Dict[str, Any]]:
    rows = [row.as_dict() for row in list_rows(ScenarioRun, limit=limit)]
    return [r for r in rows if r["dataset_key"] == key]


# ---------------------------------------------------------------------------
# Rate cards (per dataset)
# ---------------------------------------------------------------------------
def save_rate_card(key: str, payload: Dict[str, Any], engineer: str = "anonymous") -> Dict[str, Any]:
    with session() as db:
        row = db.execute(select(RateCardEntry).where(RateCardEntry.dataset_key == key)).scalar_one_or_none()
        if row is None:
            row = RateCardEntry(
                dataset_key=key,
                payload=json.dumps(payload, default=str),
                engineer=engineer[:120],
            )
            db.add(row)
        else:
            row.payload = json.dumps(payload, default=str)
            row.engineer = engineer[:120]
            row.updated_at = utcnow()
    return load_rate_card(key) or {}


def load_rate_card(key: str) -> Optional[Dict[str, Any]]:
    with session() as db:
        row = db.execute(select(RateCardEntry).where(RateCardEntry.dataset_key == key)).scalar_one_or_none()
    return row.as_dict() if row else None


def delete_rate_card(key: str) -> bool:
    with session() as db:
        row = db.execute(select(RateCardEntry).where(RateCardEntry.dataset_key == key)).scalar_one_or_none()
        if row is None:
            return False
        db.delete(row)
    return True


# ---------------------------------------------------------------------------
# Saved analysis runs
# ---------------------------------------------------------------------------
def record_analysis(key: str, payload: Dict[str, Any], seconds: float, status: str = "complete") -> Dict[str, Any]:
    row = insert_row(
        AnalysisRun(
            dataset_key=key,
            dataset_id=dataset_id(key),
            status=status,
            seconds=seconds,
            payload=json.dumps(payload, default=str),
        )
    )
    return row.as_dict()


def latest_analysis(key: str) -> Optional[Dict[str, Any]]:
    with session() as db:
        row = (
            db.execute(
                select(AnalysisRun)
                .where(AnalysisRun.dataset_key == key)
                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            )
            .scalars()
            .first()
        )
    return row.as_dict() if row else None


def analysis_history(key: str, limit: int = 10) -> List[Dict[str, Any]]:
    with session() as db:
        rows = (
            db.execute(
                select(AnalysisRun)
                .where(AnalysisRun.dataset_key == key)
                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [r.as_dict() for r in rows]


# ---------------------------------------------------------------------------
# Active-dataset preference (server-side, so a restart lands in the same case)
# ---------------------------------------------------------------------------
def get_active_key(fallback: str = "") -> str:
    with session() as db:
        row = db.get(AppPreference, "active_dataset")
    if row is None or not row.value:
        return fallback
    if find_record(row.value) is None:
        # The remembered dataset no longer exists: fall back rather than returning
        # a key that would make every page request a phantom dataset.
        return fallback
    return row.value


def set_active_key(key: str) -> str:
    with session() as db:
        row = db.get(AppPreference, "active_dataset")
        if row is None:
            db.add(AppPreference(key="active_dataset", value=key))
        else:
            row.value = key
            row.updated_at = utcnow()
    return key


# ---------------------------------------------------------------------------
# Deletion (explicit user action only; history is retained)
# ---------------------------------------------------------------------------
def mark_source_removed(key: str) -> None:
    with session() as db:
        row = db.execute(select(DatasetRecord).where(DatasetRecord.key == key)).scalar_one_or_none()
        if row is not None:
            row.present = False
            row.status = "file_missing"
            row.updated_at = utcnow()


def uploaded_file_path(key: str) -> Optional[Path]:
    """The CSV on disk for an uploaded dataset key, if it exists."""
    if not key.startswith("user:"):
        return None
    from .catalog import USER_UPLOAD_DIR

    stem = key.split(":", 1)[1]
    candidate = USER_UPLOAD_DIR / f"{stem}.csv"
    return candidate if candidate.exists() else None
