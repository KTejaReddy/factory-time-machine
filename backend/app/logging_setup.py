"""Logging configuration - human readable console logs plus a rotating file."""

from __future__ import annotations

import logging
import logging.handlers
import time
from typing import Any, Dict

from .config import CACHE_DIR, ensure_dirs, settings

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s", datefmt="%H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        CACHE_DIR / "backend.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True


def stage_timer() -> "StageTimer":
    return StageTimer()


class StageTimer:
    """Small helper used by the ingestion pipeline to report progress + timing."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.lap_started = self.started
        self.laps: Dict[str, float] = {}

    def lap(self, name: str) -> float:
        now = time.perf_counter()
        elapsed = now - self.lap_started
        self.laps[name] = round(elapsed, 3)
        self.lap_started = now
        logging.getLogger("ftm.timing").debug("stage %s took %.3fs", name, elapsed)
        return elapsed

    def as_dict(self) -> Dict[str, Any]:
        return {"total_seconds": round(time.perf_counter() - self.started, 3), "stages": dict(self.laps)}
