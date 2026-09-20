"""Dataset upload endpoint.

The flow this closes: UPLOAD -> DATASET ID -> PROFILER -> CAPABILITY MAP ->
CATALOG. A saved CSV lives in ``data/user_datasets`` and is picked up by the
next catalog build under the key ``user:<file stem>``, where the profiler infers
its structure and the capability map says what it supports (and why the rest is
switched off). ``/api/datasets/overview`` then lists it like any other dataset.

Safety rules, all of which were absent from the first version:

* only ``.csv`` is accepted - the profiler reads tabular data;
* the filename is reduced to its basename, so ``../../app/main.py`` cannot
  escape the upload directory;
* the body is read through a size cap instead of being buffered whole;
* the file must parse as a CSV before it is offered to the catalog, so a
  corrupt upload is rejected with a sentence instead of quietly becoming a
  catalog error.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import DATA_DIR
from ..services.catalog import catalog

router = APIRouter(prefix="/api/upload", tags=["upload"])

# Anchored to the project root so the directory does not depend on the CWD the
# server was started from (the catalog reads the same constant).
UPLOAD_DIR = DATA_DIR / "user_datasets"
#: 25 MB of CSV is millions of rows - far more than the profiler or the UI need.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_CHUNK = 256 * 1024


def _safe_csv_name(name: str | None) -> str:
    """Reduce an upload's filename to a bare ``*.csv`` name."""
    if not name:
        raise HTTPException(status_code=400, detail="The uploaded file has no name.")
    # Treat both separators as separators regardless of host OS.
    base = Path(name.replace("\\", "/")).name.strip()
    # A path ending in a separator (or all separators) would leave nothing.
    if not base or base in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"'{name}' is not a usable file name.")
    if Path(base).suffix.lower() != ".csv":
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{base}' is not a CSV file. Dataset upload accepts .csv files - export your "
                "table to CSV and try again."
            ),
        )
    return base


def _oversize_message(seen: int) -> str:
    limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
    return (
        f"That file is {seen / (1024 * 1024):.1f} MB, which is above the {limit_mb:.0f} MB limit "
        "for dataset uploads. Please export a smaller sample."
    )


async def _read_capped(file: UploadFile) -> bytes:
    """Receive an upload in chunks, refusing to buffer more than the cap."""
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_oversize_message(declared))
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_oversize_message(total))
        chunks.append(chunk)
    return b"".join(chunks)


def _check_parses(data: bytes, name: str) -> None:
    """A file that cannot be read as a table is rejected before it is stored."""
    if not data.strip():
        raise HTTPException(status_code=400, detail=f"'{name}' is empty.")
    try:
        parsed = pd.read_csv(io.BytesIO(data), nrows=5)
    except Exception as exc:  # pandas raises several unrelated types
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' could not be read as a CSV table: {exc}. Check the file's separator and header row.",
        ) from exc
    if parsed.shape[1] < 1:
        raise HTTPException(status_code=400, detail=f"'{name}' has no columns.")


@router.post("/")
async def upload_dataset(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved: List[Dict[str, Any]] = []
    for file in files:
        name = _safe_csv_name(file.filename)
        data = await _read_capped(file)
        _check_parses(data, name)
        target = UPLOAD_DIR / name
        target.write_bytes(data)
        saved.append({"file": name, "bytes": len(data)})

    # Rebuild so the new file is profiled and visible immediately. The build
    # itself is cheap: the built-in archives come from the parquet cache and the
    # profiler reads only the uploaded CSVs.
    catalog.build()

    uploaded: List[Dict[str, Any]] = []
    for entry in saved:
        key = f"user:{Path(entry['file']).stem}"
        summary = next((d for d in catalog.dataset_summaries() if d["key"] == key), None)
        capabilities = catalog.capabilities(key)
        from ..services import workspace

        record = workspace.find_record(key) or {}
        uploaded.append(
            {
                "key": key,
                "dataset_id": record.get("id"),
                "status": record.get("status"),
                "status_label": record.get("status_label"),
                "uploaded_at": record.get("uploaded_at"),
                "label": summary["label"] if summary else key,
                "rows": summary["rows"] if summary else 0,
                "columns": summary["columns"] if summary else 0,
                "capabilities": capabilities,
                "next": {
                    "open_workspace": f"/dataset/{record.get('id')}" if record.get("id") else "/datasets",
                    "run_analysis": f"/api/workspace/analysis/run?key={key}",
                },
            }
        )

    return {
        "message": f"{len(saved)} file(s) uploaded and profiled.",
        "files": [entry["file"] for entry in saved],
        "uploaded": uploaded,
        "status": catalog.status.snapshot(),
    }
