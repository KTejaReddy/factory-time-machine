"""External test-image endpoints.

These images are TEST-ONLY: they are stored under ``data/external_test/`` and no
code path ever feeds them to training. Every response in this router says so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile

from ..schemas import ExternalImageEntry
from ..services import external_images, vision
from ..services.catalog import catalog

router = APIRouter(prefix="/api/external-images", tags=["external-testing"])

_TRAINING_NOTICE = (
    "External Test Image - Not Used for Training. Uploads and generated images are stored under "
    "data/external_test/ only; the training pipeline reads train.zip exclusively, so nothing here can "
    "enter training data, labels or model weights."
)


@router.get("/info")
def info() -> Dict[str, Any]:
    """Directory-level facts: separation rule, counts, supported sources."""
    entries = external_images.list_images()
    by_source: Dict[str, int] = {}
    for entry in entries:
        by_source[entry.get("source", "unknown")] = by_source.get(entry.get("source", "unknown"), 0) + 1
    return {
        "training_data": False,
        "statement": _TRAINING_NOTICE,
        "storage_directory": "data/external_test",
        "supported_formats": ["png", "jpeg"],
        "max_upload_bytes": external_images.MAX_UPLOAD_BYTES,
        "generator_kinds": ["mixed", "variation", "nonfactory"],
        "count": len(entries),
        "counts_by_source": by_source,
        "model_available": vision.available(),
    }


@router.get("/list", response_model=List[ExternalImageEntry])
def list_external_images(
    source: Optional[str] = Query(None, pattern="^(upload|generated)$"),
    limit: int = Query(60, ge=1, le=500),
) -> List[ExternalImageEntry]:
    entries = external_images.list_images(source=source)[:limit]
    return [ExternalImageEntry(**entry) for entry in entries]


#: Size of each read while receiving an upload.
_UPLOAD_CHUNK = 256 * 1024


def _oversize_message(seen: int) -> str:
    limit_mb = external_images.MAX_UPLOAD_BYTES / (1024 * 1024)
    return (
        f"This image is {seen / (1024 * 1024):.1f} MB, which is above the {limit_mb:.0f} MB limit for "
        "external test images. Please upload a smaller file (or resize it first)."
    )


async def _read_capped(file: UploadFile) -> bytes:
    """Receive an upload in chunks, refusing to buffer more than the cap.

    A plain ``await file.read()`` would hold the whole body in memory before the
    service could reject it, so an unbounded body would grow the process even
    though the request was always going to fail. Checking the declared size first
    (multipart supplies it) avoids reading at all in the common case, and the
    running total is the backstop when no size is declared.
    """
    declared = getattr(file, "size", None)
    if declared is not None and declared > external_images.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_oversize_message(declared))
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > external_images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_oversize_message(total))
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload", response_model=ExternalImageEntry)
async def upload(file: UploadFile = File(...), note: str = Query("")) -> ExternalImageEntry:
    """Store one user image as an external test image (never training data)."""
    data = await _read_capped(file)
    try:
        entry = external_images.store_upload(data, original_name=file.filename or "", note=note)
    except external_images.ExternalImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExternalImageEntry(**entry)


@router.post("/generate")
def generate(
    kind: str = Query("mixed", pattern="^(mixed|variation|nonfactory)$"),
    count: int = Query(6, ge=1, le=24),
    seed: Optional[int] = Query(None, ge=0),
) -> Dict[str, Any]:
    """Generate synthetic robustness-test images (not real industrial data)."""
    entries = external_images.generate_test_images(catalog, kind=kind, count=count, seed=seed)
    return {
        "generated": [ExternalImageEntry(**entry).model_dump() for entry in entries],
        "count": len(entries),
        "training_data": False,
        "statement": _TRAINING_NOTICE,
        "note": (
            "Synthetic images are a robustness/testing set only. They do not represent real "
            "factory conditions and carry no ground-truth labels."
        ),
    }


@router.get("/image/{image_id}")
def image(image_id: str) -> Response:
    entry = external_images.get_entry(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"external image '{image_id}' not found")
    data = external_images.image_bytes(image_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"external image '{image_id}' file is missing")
    return Response(
        content=data,
        media_type="image/png" if entry.get("format", "png") == "png" else "image/jpeg",
        headers={"Cache-Control": "private, max-age=3600", "X-Training-Data": "false"},
    )


@router.delete("/{image_id}")
def delete(image_id: str) -> Dict[str, Any]:
    if not external_images.delete_image(image_id):
        raise HTTPException(status_code=404, detail=f"external image '{image_id}' not found")
    return {"deleted": image_id, "training_data": False}


@router.post("/predict/{image_id}")
def predict(image_id: str) -> Dict[str, Any]:
    """Full inspection pipeline (prediction, TTA, uncertainty, Grad-CAM) on an external image."""
    try:
        return external_images.predict_external(catalog, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
