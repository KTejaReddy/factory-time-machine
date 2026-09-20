"""Regression guard for the shared-archive read race.

``zipfile.ZipFile`` keeps one underlying file handle and reads a member with a
seek followed by a read. Two threads reading different members through the same
handle therefore interleave, and the loser gets bytes that fail to decompress
with ``zlib.error: invalid stored block lengths``.

FastAPI runs these synchronous endpoints in a thread pool and the Inspection page
requests every thumbnail at once, so this was reachable in normal use as an
occasionally-broken image. ``Catalog.image_bytes`` now serialises on a lock.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from app.services.catalog import Catalog


def _decode(raw: bytes) -> None:
    """Fully decode, so truncated or corrupt bytes are caught, not just bad headers."""
    Image.open(io.BytesIO(raw)).load()


@pytest.fixture(scope="module")
def specimen_names(catalog) -> list[str]:
    names = [entry["name"] for entry in catalog.vision_index]
    if not names:
        pytest.skip("image archive not indexed")
    # Spread across the archive so threads touch different members.
    step = max(1, len(names) // 240)
    return names[::step][:240]


def test_concurrent_archive_reads_are_never_corrupt(catalog, specimen_names):
    def worker(name: str) -> tuple[str, object]:
        try:
            _decode(catalog.image_bytes(name))
            return "ok", name
        except Exception as exc:  # noqa: BLE001
            return "fail", f"{name}: {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(worker, specimen_names * 2))

    failures = [detail for status, detail in results if status == "fail"]
    assert not failures, f"{len(failures)}/{len(results)} concurrent archive reads were corrupt: {failures[:5]}"


def test_concurrent_thumbnails_are_never_corrupt(catalog, specimen_names):
    def worker(spec: tuple[str, int]) -> tuple[str, object]:
        name, size = spec
        try:
            _decode(catalog.thumbnail(name, size=size))
            return "ok", name
        except Exception as exc:  # noqa: BLE001
            return "fail", f"{name}@{size}: {type(exc).__name__}: {exc}"

    specs = [(name, size) for name in specimen_names for size in (96, 160, 224)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(worker, specs))

    failures = [detail for status, detail in results if status == "fail"]
    assert not failures, f"{len(failures)}/{len(results)} concurrent thumbnails were corrupt: {failures[:5]}"


def test_vision_zip_handle_is_created_once_under_concurrency():
    """The lazily built handle must be shared.

    The app holds one ``Catalog`` singleton, so if two threads raced the
    ``is None`` check the archive would be opened twice and one handle would be
    leaked for the lifetime of the process.
    """
    shared = Catalog()
    try:
        def grab(_: int) -> int:
            return id(shared.vision_zip())

        with ThreadPoolExecutor(max_workers=24) as pool:
            handles = set(pool.map(grab, range(48)))
        assert len(handles) == 1, f"{len(handles)} separate ZipFile handles were created"
    finally:
        if shared._vision_zip is not None:
            shared._vision_zip.close()


def test_thumbnail_cache_write_is_atomic(catalog, specimen_names):
    """A thumbnail must never be observed half-written: readers either miss the
    cache file entirely or read a complete PNG, never a truncated one."""
    name = specimen_names[0]
    first = catalog.thumbnail(name, size=128)

    def reader(_: int) -> bool:
        try:
            _decode(catalog.thumbnail(name, size=128))
            return True
        except Exception:  # noqa: BLE001
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(reader, range(64)))

    assert all(results), "a concurrent reader observed a partially written thumbnail"
    assert catalog.thumbnail(name, size=128) == first, "cached thumbnail changed between reads"
