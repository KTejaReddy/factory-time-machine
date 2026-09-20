"""SQLite persistence (PostgreSQL-compatible SQLAlchemy models).

Stores the dataset registry (one case file per dataset), human-in-the-loop
feedback, engineer-declared dataset linkage assumptions, rate cards, saved
analysis runs, what-if scenario runs, AI narratives and AI call logs.

Every table that holds a dataset-specific result carries ``dataset_key``: that
column is what keeps Dataset A's scenarios, feedback and analysis out of
Dataset B's case file. The schema deliberately stays portable: no SQLite-specific
types, JSON stored as TEXT, timestamps stored as UTC datetimes.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    inspect as sa_inspect,
    select,
    text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import ensure_dirs, settings

#: FastAPI runs synchronous endpoints in a thread pool, so several requests can
#: hold a connection at once. The defaults (5 + 10 overflow, 30 s wait) turned any
#: accidental connection leak into a 30-second hang on every page; a slightly
#: larger pool plus a short timeout makes a future leak fail fast and visible.
_engine_kwargs: dict = {"future": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_timeout=10)

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


def _json_load(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class DatasetRecord(Base):
    """One dataset's case file: identity, provenance and profiled structure.

    Rows survive even when the underlying file disappears (``present=False``), so
    an analysis history is never silently deleted just because a CSV was moved.
    """

    __tablename__ = "dataset_records"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), default="simulation")
    source_file: Mapped[str] = mapped_column(String(400), default="")
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(40), default="analysis_ready")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped[str] = mapped_column(Text, default="{}")
    capabilities: Mapped[str] = mapped_column(Text, default="{}")
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "source_file": self.source_file,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "status": self.status,
            "rows": self.rows,
            "columns": self.columns,
            "profile": _json_load(self.profile) or {},
            "capabilities": _json_load(self.capabilities) or {},
            "present": self.present,
            "notes": self.notes,
        }


class RateCardEntry(Base):
    """Engineer-supplied rates, stored per dataset.

    A rate card is never shared between datasets implicitly: the same rates can be
    copied explicitly by the user, but each dataset owns its own row.
    """

    __tablename__ = "rate_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    engineer: Mapped[str] = mapped_column(String(120), default="anonymous")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "rates": _json_load(self.payload) or {},
            "engineer": self.engineer,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AnalysisRun(Base):
    """A saved analysis result set for one dataset.

    Computing the full report is deliberately explicit (the user asks for it), and
    the result is stored so reopening a dataset does not silently re-run or, worse,
    re-compute a different number than the report the user already downloaded.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(200), index=True)
    dataset_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="complete")
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dataset_key": self.dataset_key,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "seconds": round(self.seconds, 2),
            "payload": _json_load(self.payload) or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FeedbackItem(Base):
    """Engineer verdict on a single AI finding."""

    __tablename__ = "feedback_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Which dataset the reviewed finding belongs to ("" for pre-registry rows).
    dataset_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    finding_id: Mapped[str] = mapped_column(String(160), index=True)
    finding_kind: Mapped[str] = mapped_column(String(60))
    finding_title: Mapped[str] = mapped_column(String(400))
    #: The exact evidence payload the engineer was shown, stored verbatim.
    payload: Mapped[str] = mapped_column(Text, default="{}")
    decision: Mapped[str] = mapped_column(String(20))  # confirmed | rejected | needs_review
    note: Mapped[str] = mapped_column(Text, default="")
    engineer: Mapped[str] = mapped_column(String(120), default="anonymous")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dataset_key": self.dataset_key,
            "finding_id": self.finding_id,
            "finding_kind": self.finding_kind,
            "finding_title": self.finding_title,
            "payload": _json_load(self.payload),
            "decision": self.decision,
            "note": self.note,
            "engineer": self.engineer,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LinkageAssumption(Base):
    """Engineer-declared bridge between the vision domain and the process domain.

    The datasets share no key (see DATASET_SCHEMA.md section 4), so any
    defect-class -> station edge is an explicit, logged assumption.
    """

    __tablename__ = "linkage_assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    defect_class: Mapped[str] = mapped_column(String(80), index=True)
    station: Mapped[str] = mapped_column(String(80), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(120), default="anonymous")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "defect_class": self.defect_class,
            "station": self.station,
            "rationale": self.rationale,
            "author": self.author,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScenarioRun(Base):
    """A stored what-if run: scenario definition, baseline and simulated result."""

    __tablename__ = "scenario_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The dataset this run was simulated for. The What-If history is filtered on
    #: it, so Dataset A's scenarios never appear under Dataset B.
    dataset_key: Mapped[str] = mapped_column(String(200), default="model3", index=True)
    name: Mapped[str] = mapped_column(String(200))
    engine: Mapped[str] = mapped_column(String(40), default="des")
    scenario: Mapped[str] = mapped_column(Text, default="{}")
    baseline: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str] = mapped_column(Text, default="{}")
    validation: Mapped[str] = mapped_column(Text, default="{}")
    engineer: Mapped[str] = mapped_column(String(120), default="anonymous")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dataset_key": self.dataset_key,
            "name": self.name,
            "engine": self.engine,
            "scenario": _json_load(self.scenario),
            "baseline": _json_load(self.baseline),
            "result": _json_load(self.result),
            "validation": _json_load(self.validation),
            "engineer": self.engineer,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Narrative(Base):
    """Cached AI narrative together with the structured evidence it was given."""

    __tablename__ = "narratives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(60), index=True)
    subject_id: Mapped[str] = mapped_column(String(200), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text, default="{}")
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "subject_id": self.subject_id,
            "provider": self.provider,
            "model": self.model,
            "content": _json_load(self.content),
            "evidence": _json_load(self.evidence),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AppPreference(Base):
    """Small persistent key/value store (the active dataset lives here).

    The UI remembers the active dataset in localStorage too, but the server-side
    copy is what makes "close the browser and reopen the application" land back in
    the same case file rather than the first dataset in the list.
    """

    __tablename__ = "app_preferences"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "value": self.value, "updated_at": self.updated_at.isoformat() if self.updated_at else None}


class AICallLog(Base):
    """Every AI API attempt is logged so cost/usage stays observable."""

    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "prompt_chars": self.prompt_chars,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
#: Columns added after the first release. ``create_all`` only creates missing
#: *tables* - it never alters an existing one - so an older database would keep
#: its tables without the dataset columns and every dataset-scoped query would
#: fail. These additions are applied in place, preserving the existing history.
_ADDED_COLUMNS = (
    ("scenario_runs", "dataset_key", "VARCHAR(200) DEFAULT 'model3'"),
    ("feedback_items", "dataset_key", "VARCHAR(200) DEFAULT ''"),
)


def _migrate_columns() -> None:
    """Add post-release columns to pre-existing tables (idempotent)."""
    if engine.dialect.name != "sqlite":  # pragma: no cover - other engines use migrations
        return
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                continue
            conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    ensure_dirs()
    Base.metadata.create_all(engine)
    _migrate_columns()


@contextmanager
def session() -> Iterator[Any]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_rows(model_: type, limit: int = 200, order_desc: bool = True) -> List[Any]:
    with session() as db:
        stmt = select(model_)
        if hasattr(model_, "created_at") and order_desc:
            stmt = stmt.order_by(model_.created_at.desc())
        stmt = stmt.limit(limit)
        return list(db.execute(stmt).scalars())


def insert_row(row: Any) -> Any:
    with session() as db:
        db.add(row)
        db.flush()
        db.refresh(row)
        return row
