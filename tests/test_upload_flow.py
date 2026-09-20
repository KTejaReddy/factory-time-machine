"""Dataset upload -> profiler -> capability map -> catalog.

These tests pin the behaviour added in the recovery pass: an uploaded CSV is
sanitised, size-capped, profiled and mapped to the same capability shape the
built-in archives use, and an upload that supports nothing still answers with
reasons instead of crashing or reporting zeros.
"""

from __future__ import annotations

import asyncio
import io

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.routers import upload as upload_router
from app.services.capabilities import FEATURES, map_capabilities
from app.services.profiler import profile_dataframe

RICH_CSV = (
    "Machine_ID,temperature_C,queue_length,cycle_time_s,scrap_cost_usd,lot,shift\n"
    "M1,181.2,3,12.5,220.0,L-1,A\n"
    "M2,176.5,0,14.1,90.5,L-2,B\n"
    "M3,190.1,7,11.8,310.0,L-3,A\n"
)

EMPTY_CSV = "note,operator,qty\nobservation 1,kim,2\nobservation 2,sam,0\n"


def _rich_frame() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(RICH_CSV))


def test_profiler_detects_concepts_behind_unfamiliar_column_names(catalog):
    prof = profile_dataframe(_rich_frame())
    m = prof["manufacturing"]
    assert m["stations"] == ["Machine_ID"]
    # cycle_time_s must be a process variable, not a timestamp: the first
    # profiler classified any column containing "time" as a timestamp.
    assert set(m["process_vars"]) == {"temperature_C", "queue_length", "cycle_time_s"}
    assert m["cost_fields"] == ["scrap_cost_usd"]
    assert m["batch_ids"] is True and m["batch_id_columns"] == ["lot"]
    assert prof["stats"]["rows"] == 3


def test_capability_map_shape_matches_the_builtin_datasets(catalog):
    builtin = catalog.capabilities("model3")["available"]
    uploaded = map_capabilities(profile_dataframe(_rich_frame()))["available"]
    assert set(builtin) == set(uploaded) == set(FEATURES)
    assert set(builtin) != set(), "the built-in capability map must not be empty"


def test_every_switched_off_capability_carries_a_reason(catalog):
    rich = map_capabilities(profile_dataframe(_rich_frame()))
    assert rich["available"]["production"] and rich["available"]["economics"]
    for feature, on in rich["available"].items():
        if not on:
            assert feature in rich["reasons"], f"{feature} is off with no reason"

    barren = map_capabilities(profile_dataframe(pd.read_csv(io.StringIO(EMPTY_CSV))))
    assert not any(barren["available"].values())
    for feature in FEATURES:
        assert feature in barren["reasons"], f"{feature} is off with no reason"


def test_capability_map_degrades_without_crashing_on_an_empty_profile(catalog):
    """A missing ``manufacturing`` section must read as "nothing detected"."""
    mapped = map_capabilities({})
    assert mapped["available"]["production"] is False
    assert "at least two numeric process columns" in mapped["reasons"]["anomaly"]


def test_upload_sanitises_names_profiles_the_file_and_lists_it(tmp_path, monkeypatch, catalog):
    from app.services import catalog as catalog_module

    monkeypatch.setattr(upload_router, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(catalog_module, "USER_UPLOAD_DIR", tmp_path)

    client = TestClient(app)
    # The filename tries to climb out of the upload directory.
    response = client.post(
        "/api/upload/",
        files={"files": ("../../machine_shift_export.csv", RICH_CSV.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    uploaded = body["uploaded"][0]
    key = "user:machine_shift_export"

    assert uploaded["key"] == key
    assert uploaded["rows"] == 3 and uploaded["columns"] == 7
    assert uploaded["capabilities"]["available"]["forensics"] is True
    assert uploaded["capabilities"]["available"]["vision"] is False

    # Stored under its bare name inside the upload directory...
    assert (tmp_path / "machine_shift_export.csv").exists()
    # ...and nothing was written where the traversal would have landed.
    assert not (tmp_path.parent.parent / "machine_shift_export.csv").exists()

    # ...and the catalog lists it with the same capability map.
    listed = client.get("/api/datasets/overview").json()["datasets"]
    entry = next((d for d in listed if d["key"] == key), None)
    assert entry is not None, "uploaded dataset missing from the overview"
    assert entry["capabilities"]["available"]["production"] is True

    # Leave the shared session catalog as it was.
    catalog.frames.pop(key, None)
    catalog.user_profiles.pop(key, None)


def test_deleting_an_uploaded_csv_drops_it_from_the_catalog(tmp_path, monkeypatch, catalog):
    """A rebuild must not keep serving a file that is no longer on disk.

    ``user_profiles`` was never cleared between builds, so removing a CSV left
    the dataset (and its capability map) in the list for the life of the process.
    """
    from app.services import catalog as catalog_module

    monkeypatch.setattr(upload_router, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(catalog_module, "USER_UPLOAD_DIR", tmp_path)
    client = TestClient(app)

    upload = client.post(
        "/api/upload/",
        files={"files": ("temporary_probe.csv", RICH_CSV.encode("utf-8"), "text/csv")},
    )
    assert upload.status_code == 200
    key = "user:temporary_probe"
    assert key in catalog.user_profiles

    (tmp_path / "temporary_probe.csv").unlink()
    catalog.build()

    assert key not in catalog.user_profiles
    assert all(d["key"] != key for d in catalog.dataset_summaries())


def test_upload_rejects_non_csv_and_unreadable_files(tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(upload_router, "UPLOAD_DIR", tmp_path)
    client = TestClient(app)

    wrong_type = client.post(
        "/api/upload/",
        files={"files": ("report.xlsx", RICH_CSV.encode("utf-8"), "application/vnd.ms-excel")},
    )
    assert wrong_type.status_code == 400
    assert "csv" in wrong_type.json()["detail"].lower()

    broken = client.post(
        "/api/upload/",
        files={"files": ("broken.csv", b"\x00\x01\x02 not a table \xff", "text/csv")},
    )
    assert broken.status_code == 400
    assert "could not be read" in broken.json()["detail"]

    assert list(tmp_path.iterdir()) == [], "a rejected upload must not be stored"


class _FakeUpload:
    """Minimal stand-in for ``UploadFile`` used by the size-cap tests."""

    def __init__(self, chunks: list[bytes], declared: int | None = None) -> None:
        self._chunks = list(chunks)
        self.size = declared

    async def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def test_declared_oversize_upload_is_refused_before_reading():
    over = upload_router.MAX_UPLOAD_BYTES + 1
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(upload_router._read_capped(_FakeUpload([b"x" * 10], declared=over)))
    assert excinfo.value.status_code == 413
    assert "limit" in excinfo.value.detail


def test_undeclared_upload_is_capped_while_streaming():
    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * (upload_router.MAX_UPLOAD_BYTES // len(chunk) + 2)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(upload_router._read_capped(_FakeUpload(chunks, declared=None)))
    assert excinfo.value.status_code == 413
