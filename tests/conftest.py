"""Shared pytest fixtures.

The backend is a plain package under ``backend/``; add it to ``sys.path`` so tests
can import ``app.*`` without an installed package. The catalog is expensive to
build (it reads two large archives), so it is built once per session.

Hermetic by construction: the suite runs with the language model disabled, so it
never touches the network and never depends on whether the developer happens to
have an API key in ``.env``. Tests that want the LLM code path enable it
explicitly through ``monkeypatch`` (see ``test_ai_fallback``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Set before ``app.config`` is imported anywhere: its .env loader uses
# ``os.environ.setdefault``, so values already present here win over .env.
os.environ["AI_DISABLE"] = "true"
os.environ["AI_API_KEY"] = ""


@pytest.fixture(autouse=True)
def workspace_rows_do_not_leak():
    """No test may leave the real workspace as it did not find it.

    The upload tests drive the real ``POST /api/upload/`` endpoint, and that
    endpoint registers a case file in the database and then rebuilds the catalog
    against whatever upload directory is patched in. Popping the key out of the
    in-process catalog was not enough. Two things leaked into the developer's
    workspace:

    * rows *created* by a test (``user:machine_shift_export``,
      ``user:temporary_probe``) stayed in the dataset history, listed as "source
      file missing" - a bookkeeping artefact that reads like a product bug;
    * rows *modified* by a test (a build against an empty patched directory
      flagged the real datasets as missing) kept the wrong presence flag.

    So the fixture restores the exact state of every pre-existing row and deletes
    everything the test created, together with the artifacts saved against it.
    """
    from sqlalchemy import delete, select

    from app.db import AnalysisRun, DatasetRecord, FeedbackItem, RateCardEntry, ScenarioRun, session, utcnow

    fields = ("name", "kind", "source_file", "uploaded_at", "status", "rows", "columns", "profile", "capabilities", "present")
    with session() as db:
        before = {
            row.key: {field: getattr(row, field) for field in fields}
            for row in db.execute(select(DatasetRecord)).scalars()
        }
    yield
    with session() as db:
        for row in db.execute(select(DatasetRecord)).scalars():
            if row.key in before:
                for field, value in before[row.key].items():
                    setattr(row, field, value)
                row.updated_at = utcnow()
                continue
            for model in (AnalysisRun, RateCardEntry, FeedbackItem, ScenarioRun):
                db.execute(delete(model).where(model.dataset_key == row.key))
            db.delete(row)


@pytest.fixture(scope="session")
def catalog():
    """The real dataset catalog, built from the supplied archives.

    Skips the test module (rather than failing) when the archives are absent, so
    a fresh clone without the 450 MB of data still reports a useful result.
    """
    from app.config import DES_ZIP, VISION_ZIP
    from app.services.catalog import catalog as singleton

    if not VISION_ZIP.exists() or not DES_ZIP.exists():
        pytest.skip("datasets not present: run tests where train.zip and the DES zip live")

    singleton.build()
    assert singleton.status.snapshot()["state"] == "ready", singleton.status.snapshot()
    return singleton
