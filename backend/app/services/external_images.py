"""External test-image service.

Purpose
-------
A safe place to test the vision model with images that were **not** part of the
training data: user uploads, generated synthetic variants of archive specimens,
and deliberately non-manufacturing images.

The one rule this module enforces structurally, not just by convention:

    NOTHING in data/external_test is ever read by the training pipeline.

``train_vision.py`` builds its cache exclusively from ``train.zip``
(``vision_model.build_cache`` opens the archive itself); this directory is not on
any code path that touches training, labels or model weights. The dataset
separation is real, not decorative:

    data/
    ├── train.zip                    (training source, read-only)
    └── external_test/               (test-only; never fed back)

Uploads are validated (PNG/JPEG magic bytes, size cap, no path components) and
stored under server-generated UUID names, so a hostile filename can never reach
the filesystem. Every stored image keeps a provenance record saying where it
came from.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import struct
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR
from .catalog import Catalog
from . import vision

log = logging.getLogger("ftm.external")

#: Everything lives under this one directory. It is deliberately NOT inside the
#: training cache and NOT read by vision_model.build_cache().
EXTERNAL_DIR = DATA_DIR / "external_test"
INDEX_FILE = EXTERNAL_DIR / "index.json"

#: 10 MB is far above any reasonable test image; the cap exists so a client
#: cannot stream an unbounded body into memory.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

MAX_EXTERNAL_IMAGES = 500  # keep the demo directory bounded

SOURCE_UPLOAD = "upload"
SOURCE_GENERATED = "generated"

#: Magic-byte signatures of the formats we accept. Checking the header (not the
#: filename, not the Content-Type header the client claims) is what stops a
#: renamed executable or an HTML file from being stored and later served back.
_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
)

_PNG_IEND = b"IEND"


class ExternalImageError(ValueError):
    """Raised for invalid uploads; the API maps this to HTTP 400."""


def _ensure_dir() -> None:
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)


def _read_index() -> List[Dict[str, Any]]:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_index(entries: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    INDEX_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sniff_format(data: bytes) -> str:
    """Return 'png' or 'jpeg' from magic bytes, else raise.

    Content-Type headers are client-asserted and never trusted; the bytes are.
    """
    for magic, fmt in _MAGICS:
        if data.startswith(magic):
            return fmt
    raise ExternalImageError(
        "Unsupported file type. Only PNG and JPEG images are accepted "
        "(checked from the file's actual bytes, not its name)."
    )


def _png_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    if not data.startswith(_MAGICS[0][0]):
        return None
    if b"IHDR" not in data[:33]:
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    except struct.error:
        return None


def _validate_and_normalize(data: bytes, max_side: int = 512) -> tuple[bytes, Dict[str, Any]]:
    """Decode, sanity-check and (if needed) downscale an image.

    Decoding with PIL proves the payload is a real, complete image rather than
    bytes that merely *begin* like one. Very large images are downscaled so the
    demo directory stays light and Grad-CAM stays fast.
    """
    from PIL import Image

    fmt = _sniff_format(data)
    meta: Dict[str, Any] = {"format": fmt, "stored_bytes": len(data)}

    if fmt == "png":
        if len(data) > MAX_UPLOAD_BYTES or _PNG_IEND not in data[-16:]:
            # A PNG that never terminates is either corrupt or a truncated upload.
            if _PNG_IEND not in data:
                raise ExternalImageError("The PNG file appears truncated or corrupt.")
        dims = _png_dimensions(data)
        if dims:
            meta["width"], meta["height"] = dims

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ExternalImageError(f"The file could not be decoded as an image ({type(exc).__name__}).") from exc

    meta["width"], meta["height"] = img.size
    meta["mode"] = img.mode

    if max(img.size) > max_side:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        meta["downscaled_to"] = list(img.size)
        meta["stored_bytes"] = len(data)
        meta["format"] = "png"

    return data, meta


def _prune_to_limit(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the oldest generated images when the store exceeds its cap."""
    if len(entries) <= MAX_EXTERNAL_IMAGES:
        return entries
    overflow = len(entries) - MAX_EXTERNAL_IMAGES
    removable = [e for e in entries if e.get("source") == SOURCE_GENERATED]
    removed_ids = set()
    for entry in removable[:overflow]:
        removed_ids.add(entry["id"])
        _delete_file(entry["file"])
    entries = [e for e in entries if e["id"] not in removed_ids]
    # If uploads alone still exceed the cap, drop the oldest uploads too.
    while len(entries) > MAX_EXTERNAL_IMAGES:
        entry = entries.pop(0)
        _delete_file(entry["file"])
    return entries


def _delete_file(filename: str) -> None:
    try:
        (EXTERNAL_DIR / filename).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not delete external image %s: %s", filename, exc)


def store_upload(data: bytes, original_name: str = "", note: str = "") -> Dict[str, Any]:
    """Validate and store one uploaded image. Returns its index entry."""
    if not data:
        raise ExternalImageError("No image data was received.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExternalImageError(
            f"This image is {len(data) / (1024 * 1024):.1f} MB, which is above the "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit for external test images. "
            "Please upload a smaller file (or resize it first)."
        )

    data, meta = _validate_and_normalize(data)

    image_id = uuid.uuid4().hex[:12]
    filename = f"upload_{image_id}.png"

    entries = _read_index()
    entry = {
        "id": image_id,
        "file": filename,
        "original_name": Path(original_name or "unnamed").name[:120],
        "source": SOURCE_UPLOAD,
        "note": note[:400],
        "training_data": False,  # explicit, machine-checkable statement
        "created_at": _now_iso(),
        **meta,
    }
    _ensure_dir()
    (EXTERNAL_DIR / filename).write_bytes(data)
    entries.append(entry)
    entries = _prune_to_limit(entries)
    _write_index(entries)
    log.info("stored external upload %s (%s, %d bytes)", image_id, entry["original_name"], len(data))
    return entry


def list_images(source: Optional[str] = None) -> List[Dict[str, Any]]:
    entries = _read_index()
    if source:
        entries = [e for e in entries if e.get("source") == source]
    return sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)


def get_entry(image_id: str) -> Optional[Dict[str, Any]]:
    for entry in _read_index():
        if entry["id"] == image_id:
            return entry
    return None


def image_bytes(image_id: str) -> Optional[bytes]:
    entry = get_entry(image_id)
    if entry is None:
        return None
    try:
        return (EXTERNAL_DIR / entry["file"]).read_bytes()
    except OSError:
        return None


def delete_image(image_id: str) -> bool:
    entries = _read_index()
    remaining = [e for e in entries if e["id"] != image_id]
    if len(remaining) == len(entries):
        return False
    for entry in entries:
        if entry["id"] == image_id:
            _delete_file(entry["file"])
    _write_index(remaining)
    return True


# ---------------------------------------------------------------------------
# Prediction (reuses the exact pipeline used for archive specimens)
# ---------------------------------------------------------------------------
def predict_external(catalog: Catalog, image_id: str) -> Dict[str, Any]:
    """Run the full inspection pipeline on a stored external image.

    The prediction logic is *the same code path* as ``vision.inspect`` (preprocess
    -> CNN -> TTA -> uncertainty -> Grad-CAM); the only difference is where the
    bytes come from and that the result is explicitly labelled as an external
    test image.
    """
    entry = get_entry(image_id)
    if entry is None:
        raise KeyError(f"external image '{image_id}' not found")
    data = image_bytes(image_id)
    if data is None:
        raise KeyError(f"external image '{image_id}' is stored in the index but its file is missing")

    if not vision.available():
        raise RuntimeError(
            "No trained vision model is available. Train one first with "
            "`python backend/training/train_vision.py`; external-image testing reports real model output only."
        )

    result = vision.predict_bytes(data)
    result["external"] = {
        "id": entry["id"],
        "original_name": entry.get("original_name"),
        "source": entry.get("source"),
        "note": entry.get("note", ""),
        "created_at": entry.get("created_at"),
        "training_data": False,
        "statement": "External Test Image - Not Used for Training",
    }
    result["specimen"] = f"external:{entry['id']}"
    return result


# ---------------------------------------------------------------------------
# Synthetic test-image generator
# ---------------------------------------------------------------------------
def generate_test_images(
    catalog: Catalog,
    kind: str = "mixed",
    count: int = 6,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Create synthetic test images and store them as external test images.

    Kinds
    -----
    ``variation``   archive specimens transformed (brightness / rotation / flip /
                    blur / noise / contrast) - "known-looking" robustness probes.
    ``nonfactory``  simple non-manufacturing shapes (a face, a car silhouette, a
                    tree, a phone, abstract noise) - out-of-distribution probes.
    ``mixed``       both, interleaved.

    These images are a *robustness set*, not real industrial data, and nothing in
    the UI or API claims otherwise. They are never added to training data.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    rng = np.random.default_rng(seed)
    count = max(1, min(count, 24))
    _ensure_dir()
    entries: List[Dict[str, Any]] = []

    archive_names = [e["name"] for e in catalog.vision_index] or []

    def _random_specimen() -> Optional[tuple[Image.Image, str]]:
        """One archive image + its class, read through the serialised zip lock."""
        if not archive_names:
            return None
        name = archive_names[int(rng.integers(0, len(archive_names)))]
        raw = catalog.image_bytes(name)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # keep the class of the source specimen for the record
        cls = name.split("/")[1] if "/" in name else "unknown"
        return img, cls

    def _vary(img: Image.Image) -> tuple[Image.Image, str]:
        arr = np.asarray(img, dtype="float32") / 255.0
        effect = rng.integers(0, 6)
        if effect == 0:  # brightness
            factor = float(rng.uniform(0.4, 1.6))
            arr = np.clip(arr * factor, 0, 1)
            label = f"brightness x{factor:.2f}"
        elif effect == 1:  # contrast
            factor = float(rng.uniform(0.4, 1.8))
            arr = np.clip((arr - 0.5) * factor + 0.5, 0, 1)
            label = f"contrast x{factor:.2f}"
        elif effect == 2:  # gaussian noise
            sigma = float(rng.uniform(0.03, 0.12))
            arr = np.clip(arr + rng.normal(0, sigma, arr.shape), 0, 1)
            label = f"gaussian noise sigma={sigma:.2f}"
        elif effect == 3:  # horizontal flip
            label = "horizontal flip"
        elif effect == 4:  # rotation
            label = "rotation"
        else:  # blur
            label = "blur"
        out = Image.fromarray((arr * 255).astype("uint8"))
        if effect == 3:
            out = out.transpose(Image.FLIP_LEFT_RIGHT)
        elif effect == 4:
            out = out.rotate(float(rng.uniform(-40, 40)), fillcolor=(0, 0, 0))
        elif effect == 5:
            out = out.filter(ImageFilter.GaussianBlur(radius=float(rng.uniform(0.8, 2.4))))
        return out, label

    def _nonfactory() -> tuple[Image.Image, str]:
        what = int(rng.integers(0, 5))
        size = 256
        img = Image.new("RGB", (size, size), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        if what == 0:  # a simple face
            draw.ellipse([48, 40, 208, 200], fill=(240, 200, 160), outline=(120, 90, 60), width=4)
            draw.ellipse([88, 96, 116, 124], fill=(40, 40, 40))
            draw.ellipse([140, 96, 168, 124], fill=(40, 40, 40))
            draw.arc([88, 120, 168, 176], 20, 160, fill=(120, 60, 60), width=6)
            label = "synthetic face (non-manufacturing)"
        elif what == 1:  # a car silhouette
            draw.rectangle([30, 140, 226, 186], fill=(60, 90, 160))
            draw.polygon([(70, 140), (105, 100), (170, 100), (200, 140)], fill=(60, 90, 160))
            draw.ellipse([55, 168, 95, 208], fill=(20, 20, 20))
            draw.ellipse([160, 168, 200, 208], fill=(20, 20, 20))
            label = "synthetic car (non-manufacturing)"
        elif what == 2:  # a tree
            draw.rectangle([118, 140, 138, 220], fill=(110, 80, 50))
            draw.ellipse([60, 40, 196, 160], fill=(60, 140, 70))
            label = "synthetic tree (non-manufacturing)"
        elif what == 3:  # a phone
            draw.rounded_rectangle([88, 28, 168, 228], radius=16, fill=(30, 30, 35), outline=(90, 90, 100), width=3)
            draw.rectangle([98, 48, 158, 198], fill=(120, 160, 200))
            label = "synthetic phone (non-manufacturing)"
        else:  # abstract noise block
            noise = rng.integers(0, 256, (size, size, 3), dtype="uint8")
            img = Image.fromarray(noise, "RGB")
            label = "random noise pattern (non-manufacturing)"
        return img, label

    made = 0
    i = 0
    while made < count and i < count * 4:
        i += 1
        want_nonfactory = kind == "nonfactory" or (kind == "mixed" and made % 2 == 1)
        try:
            if want_nonfactory or not archive_names:
                img, description = _nonfactory()
                source_label = "generated:nonfactory"
            else:
                picked = _random_specimen()
                if picked is None:
                    continue
                img, cls = picked
                img, effect_label = _vary(img)
                description = f"{cls} specimen with {effect_label}"
                source_label = "generated:archive_variation"
        except Exception as exc:  # noqa: BLE001 - one bad draw must not abort the batch
            log.warning("skipping a generated test image: %s", exc)
            continue

        image_id = uuid.uuid4().hex[:12]
        filename = f"gen_{image_id}.png"
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        entry = {
            "id": image_id,
            "file": filename,
            "original_name": filename,
            "source": SOURCE_GENERATED,
            "source_detail": source_label,
            "description": description,
            "note": description,
            "training_data": False,
            "created_at": _now_iso(),
            "format": "png",
            "width": img.size[0],
            "height": img.size[1],
            "stored_bytes": len(data),
        }
        (EXTERNAL_DIR / filename).write_bytes(data)
        entries.append(entry)
        made += 1

    all_entries = _read_index() + entries
    all_entries = _prune_to_limit(all_entries)
    _write_index(all_entries)
    log.info("generated %d external test images (kind=%s)", len(entries), kind)
    return entries



