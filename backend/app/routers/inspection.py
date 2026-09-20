"""Visual inspection endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Response

from ..services import vision
from ..services.catalog import catalog

router = APIRouter(prefix="/api/inspection", tags=["inspection"])


@router.get("/metrics")
def metrics() -> Dict[str, Any]:
    return vision.vision_metrics_payload(catalog)


@router.get("/samples")
def samples(per_class: int = Query(8, ge=1, le=50)) -> Dict[str, Any]:
    return {
        "classes": catalog.vision_classes,
        "samples": vision.sample_specimens(catalog, per_class=per_class),
        "model_available": vision.available(),
        "note": (
            "Labels come from the archive's folder structure. No product, batch or station identifier exists, "
            "and no bounding box or mask is provided."
        ),
    }


@router.get("/image")
def image(name: str = Query(..., description="archive path, e.g. train/crack/crack_00000.png")) -> Response:
    entry = catalog.find_specimen(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"'{name}' is not in the image archive")
    data = catalog.image_bytes(entry["name"])
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Dataset-Class": entry["class"]},
    )


@router.get("/thumbnail")
def thumbnail(
    name: str = Query(..., description="archive path"),
    size: int = Query(160, ge=32, le=512),
) -> Response:
    entry = catalog.find_specimen(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"'{name}' is not in the image archive")
    data = catalog.thumbnail(entry["name"], size=size)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Dataset-Class": entry["class"]},
    )


@router.post("/predict")
def predict(specimen: str = Query(..., description="archive path or class/index, e.g. crack/0")) -> Dict[str, Any]:
    try:
        return vision.inspect(catalog, specimen)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        # No trained model yet: say so plainly instead of returning a fake prediction.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
