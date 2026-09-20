"""External test-image feature.

The core property under test is separation: nothing created by this module can
ever reach the training pipeline. The training cache builder
(``vision_model.build_cache``) reads ``train.zip`` only, so the assertions here
check that

* every stored entry is flagged ``training_data=False``,
* uploads are validated from actual bytes (a renamed text file is rejected),
* hostile filenames cannot reach the filesystem (server-generated names),
* predictions on external images use the same pipeline and say they are external,
* generated images cover both in-distribution variations and non-factory shapes.

The vision model is loaded once per module (torch import + weights) and shared.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import external_images, vision


def _png_bytes(size: int = 64, color=(180, 160, 140)) -> bytes:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    img = Image.new("RGB", (48, 48), (10, 200, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def trained(catalog):
    """Skip the prediction tests when no vision model has been trained."""
    if not vision.available():
        pytest.skip("vision model not trained (data/cache/vision_cnn.pt missing)")


@pytest.fixture(scope="module")
def one_upload(catalog, trained):
    entry = external_images.store_upload(_png_bytes(), original_name="tiny test.png", note="pytest")
    yield entry
    external_images.delete_image(entry["id"])


# ---------------------------------------------------------------------------
# separation rule
# ---------------------------------------------------------------------------
def test_every_entry_is_marked_not_training_data(catalog):
    external_images.store_upload(_png_bytes(), original_name="sep.png")
    for entry in external_images.list_images():
        assert entry["training_data"] is False, (
            f"external image {entry['id']} must be flagged training_data=False"
        )


def test_training_cache_never_reads_the_external_directory(catalog):
    """The separation must be structural: the training source is train.zip, and
    the external store lives outside any path training touches."""
    from app.services import vision_model

    assert "train.zip" in str(vision_model.VISION_ZIP)
    assert str(external_images.EXTERNAL_DIR).replace("\\", "/") not in str(vision_model.CACHE_ARRAY).replace("\\", "/")
    assert external_images.EXTERNAL_DIR.exists() or not external_images.list_images()


# ---------------------------------------------------------------------------
# upload validation
# ---------------------------------------------------------------------------
def test_valid_png_upload_is_stored(one_upload):
    assert one_upload["source"] == "upload"
    assert one_upload["format"] == "png"
    assert one_upload["width"] == 64 and one_upload["height"] == 64


def test_jpeg_upload_is_accepted_and_converted_metadata_kept():
    entry = external_images.store_upload(_jpeg_bytes(), original_name="cam.jpg")
    try:
        assert entry["format"] == "jpeg"
    finally:
        external_images.delete_image(entry["id"])


def test_renamed_text_file_is_rejected():
    with pytest.raises(external_images.ExternalImageError):
        external_images.store_upload(b"this is definitely not an image", original_name="evil.png")


def test_empty_upload_is_rejected():
    with pytest.raises(external_images.ExternalImageError):
        external_images.store_upload(b"")


def test_truncated_png_is_rejected():
    truncated = _png_bytes()[:40]
    with pytest.raises(external_images.ExternalImageError):
        external_images.store_upload(truncated, original_name="trunc.png")


def test_oversized_upload_is_rejected():
    class BigBytes(bytes):
        def __len__(self):
            return external_images.MAX_UPLOAD_BYTES + 1

    with pytest.raises(external_images.ExternalImageError):
        external_images.store_upload(BigBytes(_png_bytes()), original_name="big.png")


def test_hostile_filename_never_reaches_the_filesystem():
    entry = external_images.store_upload(_png_bytes(), original_name="../../secrets/evil.png")
    try:
        assert "/" not in entry["file"] and ".." not in entry["file"]
        assert entry["file"].startswith("upload_")
    finally:
        external_images.delete_image(entry["id"])


def test_large_image_is_downscaled_on_storage():
    entry = external_images.store_upload(_png_bytes(size=900), original_name="big.png")
    try:
        assert entry["width"] == 900
        assert entry.get("downscaled_to") and max(entry["downscaled_to"]) <= 512
        stored = external_images.image_bytes(entry["id"])
        assert Image.open(io.BytesIO(stored)).size[0] <= 512
    finally:
        external_images.delete_image(entry["id"])


def test_delete_removes_entry_and_file(one_upload):
    entry = external_images.store_upload(_png_bytes(), original_name="doomed.png")
    assert external_images.delete_image(entry["id"]) is True
    assert external_images.get_entry(entry["id"]) is None
    assert external_images.image_bytes(entry["id"]) is None
    assert external_images.delete_image(entry["id"]) is False


def test_unknown_id_lookups_fail_cleanly():
    assert external_images.get_entry("no-such-id") is None
    assert external_images.image_bytes("no-such-id") is None


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
def test_generator_produces_both_kinds(catalog):
    entries = external_images.generate_test_images(catalog, kind="mixed", count=4, seed=7)
    try:
        assert len(entries) == 4
        details = {e.get("source_detail") for e in entries}
        assert any(str(d).startswith("generated:archive_variation") for d in details)
        assert any(str(d).startswith("generated:nonfactory") for d in details)
        for entry in entries:
            assert entry["training_data"] is False
            raw = external_images.image_bytes(entry["id"])
            assert raw and Image.open(io.BytesIO(raw)).size[0] > 0
    finally:
        for entry in entries:
            external_images.delete_image(entry["id"])


def test_generator_nonfactory_kind_only(catalog):
    entries = external_images.generate_test_images(catalog, kind="nonfactory", count=3, seed=11)
    try:
        assert all(e.get("source_detail") == "generated:nonfactory" for e in entries)
    finally:
        for entry in entries:
            external_images.delete_image(entry["id"])


def test_generator_is_deterministic_given_a_seed(catalog):
    a = external_images.generate_test_images(catalog, kind="nonfactory", count=2, seed=99)
    b = external_images.generate_test_images(catalog, kind="nonfactory", count=2, seed=99)
    try:
        assert [e["description"] for e in a] == [e["description"] for e in b]
    finally:
        for entry in a + b:
            external_images.delete_image(entry["id"])


# ---------------------------------------------------------------------------
# prediction on external images
# ---------------------------------------------------------------------------
def test_external_prediction_uses_the_same_pipeline_and_labels_itself(trained, catalog, one_upload):
    result = external_images.predict_external(catalog, one_upload["id"])
    # same contract as a normal inspection
    for field in ("label", "confidence", "probabilities", "uncertain", "tta_agreement", "attention"):
        assert field in result
    assert 0.0 <= result["confidence"] <= 1.0
    assert 0.0 <= result["tta_agreement"] <= 1.0
    # ...but explicitly external
    assert result["external"]["training_data"] is False
    assert "not used for training" in result["external"]["statement"].lower()
    assert result["specimen"].startswith("external:")
    assert "not part of the training" in result["dataset_provenance"]["split"]


def test_external_prediction_on_a_nonfactory_image_runs_without_crashing(trained, catalog):
    entries = external_images.generate_test_images(catalog, kind="nonfactory", count=1, seed=3)
    try:
        result = external_images.predict_external(catalog, entries[0]["id"])
        # The system reports the *actual* model output. A non-factory image is not
        # automatically declared "unknown" unless the uncertainty mechanism says so.
        assert result["label"] in vision.CLASSES
        assert isinstance(result["uncertain"], bool)
        assert result["uncertainty_reason"]
    finally:
        external_images.delete_image(entries[0]["id"])


def test_predict_missing_external_image_raises_keyerror(trained, catalog):
    with pytest.raises(KeyError):
        external_images.predict_external(catalog, "no-such-id")


# ---------------------------------------------------------------------------
# byte-level inference helpers
# ---------------------------------------------------------------------------
def test_predict_bytes_returns_prediction(trained):
    result = vision.predict_bytes(_png_bytes(size=96))
    assert result["label"] in vision.CLASSES
    assert result["dataset_provenance"]["split"] == "external (not part of the training split)"


def test_ood_guard_flags_a_non_factory_image_and_not_a_normal_one(trained, catalog):
    """The guard added after the audit campaign.

    A plain CNN is confidently wrong on unfamiliar images (the campaign measured a
    synthetic face scored as 'crack' at 100%), and flip-agreement cannot detect it
    because the mirror of an unfamiliar image is still unfamiliar. The embedding
    distance does detect it. Calibration measured in-distribution at <= 0.029 and
    non-factory shapes at >= 0.040, so both sides are asserted here.
    """
    # in-distribution: a real archive specimen
    specimen = catalog.vision_index[0]["name"]
    normal = vision.predict_bytes(catalog.image_bytes(specimen))
    assert normal["ood_distance"] is not None, "the OOD guard must report a distance for archive images"
    assert normal["ood_distance"] < normal["ood_threshold"], (
        f"an archive specimen measured {normal['ood_distance']:.4f}, above the {normal['ood_threshold']} threshold"
    )
    assert normal["out_of_distribution"] is False

    # out-of-distribution: a synthetic non-factory shape
    import io as _io

    from PIL import Image as _Image
    from PIL import ImageDraw as _ImageDraw

    face = _Image.new("RGB", (256, 256), (240, 240, 240))
    draw = _ImageDraw.Draw(face)
    draw.ellipse([60, 50, 196, 190], fill=(240, 200, 160), outline=(120, 90, 60), width=4)
    buf = _io.BytesIO()
    face.save(buf, format="PNG")
    ood = vision.predict_bytes(buf.getvalue())
    assert ood["out_of_distribution"] is True, (
        f"a synthetic face measured {ood['ood_distance']:.4f}, below the {ood['ood_threshold']} threshold"
    )
    assert ood["uncertain"] is True, "an out-of-distribution image must not be presented as a confident call"
    assert "training data" in ood["uncertainty_reason"]


# ---------------------------------------------------------------------------
# upload size guard (router level)
# ---------------------------------------------------------------------------
class _FakeUpload:
    """Minimal stand-in for Starlette's UploadFile, so the guard is tested without
    standing up the whole app and its dataset catalog."""

    def __init__(self, payload: bytes, declared: int | None = None, chunk: int = 64):
        self._payload = payload
        self._offset = 0
        self._chunk = chunk
        self.size = declared
        self.reads = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        step = self._chunk if size is None or size < 0 else min(size, self._chunk)
        part = self._payload[self._offset : self._offset + step]
        self._offset += len(part)
        return part


def test_upload_guard_rejects_from_the_declared_size_without_buffering():
    """A multipart part tells us its size, so an oversized upload is refused before
    any of it is copied into process memory."""
    import asyncio

    from fastapi import HTTPException

    from app.routers import external_images as router

    over = external_images.MAX_UPLOAD_BYTES + 1
    fake = _FakeUpload(b"x" * 100, declared=over)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(router._read_capped(fake))
    assert excinfo.value.status_code == 413
    assert "limit" in excinfo.value.detail
    assert fake.reads == 0, "nothing should be read once the declared size is too large"


def test_upload_guard_stops_mid_stream_when_no_size_is_declared():
    """Without a declared size the running total is the backstop: the read stops as
    soon as the cap is passed instead of growing unbounded."""
    import asyncio

    from fastapi import HTTPException

    from app.routers import external_images as router

    chunk = 256 * 1024
    payload = b"y" * (external_images.MAX_UPLOAD_BYTES + 3 * 1024 * 1024)
    fake = _FakeUpload(payload, declared=None, chunk=chunk)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(router._read_capped(fake))
    assert excinfo.value.status_code == 413
    assert fake._offset < len(payload), "the guard must stop reading, not consume the whole body"
    # Bounded work: the cap plus at most one chunk in flight.
    assert fake._offset <= external_images.MAX_UPLOAD_BYTES + chunk


def test_upload_guard_passes_a_normal_sized_image_through():
    import asyncio

    from app.routers import external_images as router

    payload = _png_bytes(size=32)
    assert asyncio.run(router._read_capped(_FakeUpload(payload, declared=len(payload), chunk=8))) == payload


def test_uncertainty_flag_is_consistent_with_its_reason(trained, catalog, one_upload):
    result = external_images.predict_external(catalog, one_upload["id"])
    if result["uncertain"]:
        assert result["uncertainty_reason"] != "confidence and flip agreement are both above threshold"
    else:
        assert result["uncertainty_reason"] == "Confidence and flip agreement are high, and image is in-distribution."
