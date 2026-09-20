"""JSON encoding helpers.

The analytics are written with numpy, and numpy scalars (``np.int64``,
``np.float32``, ...) are not JSON serialisable by the standard encoder. That
turned a perfectly successful computation into a 500: ``/api/production/
demand-response`` for Models 1 and 2 returned ``factor_levels`` as numpy ints.

Rather than rely on every helper remembering to cast, the application's default
response class understands numpy. Endpoints that also hand evidence to the AI
client can use :func:`to_native` directly.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
from starlette.responses import JSONResponse


def to_native(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays (and NaN) to JSON-safe values."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, np.generic):
        return to_native(value.item())
    if isinstance(value, np.ndarray):
        return [to_native(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {to_native(key): to_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_native(item) for item in value]
    return value


def _default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class NumpySafeJSONResponse(JSONResponse):
    """``JSONResponse`` that can render numpy values instead of failing."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=_default,
        ).encode("utf-8")


def finite_or_none(value: Any) -> Any:
    """A float that is NaN/inf becomes ``None`` - never ``NaN`` in JSON.

    ``allow_nan=False`` (kept, because ``NaN``/``Infinity`` are not valid JSON and
    ``JSON.parse`` rejects them in the browser) raises on those values, so callers
    computing statistics should pass them through here.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return to_native(value)
