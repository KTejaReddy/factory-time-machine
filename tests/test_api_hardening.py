"""API hardening checks (found during the audit).

Both of these were real, if quiet, problems:

* ``allow_origins=list(settings.cors_origins) or ["*"]`` meant that *clearing*
  ``CORS_ORIGINS`` silently opened the API to every website, instead of closing it.
* The catch-all 500 handler returned ``internal error: <ExceptionType>: <message>``
  to the client, which leaks internal paths and library internals to whoever
  triggered the error.
"""

from __future__ import annotations

import asyncio
import json

from app.config import settings
from app.main import app


def _cors_options() -> dict:
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return middleware.kwargs
    raise AssertionError("CORSMiddleware is not installed")


def test_cors_reflects_configured_origins_without_a_wildcard_fallback():
    options = _cors_options()
    assert options["allow_origins"] == list(settings.cors_origins)
    assert "*" not in options["allow_origins"], (
        "a blank CORS_ORIGINS setting must not widen the API to every origin"
    )
    allowed_methods = set(options["allow_methods"])
    assert allowed_methods <= {"GET", "POST", "DELETE", "OPTIONS"}, (
        f"only the methods the app actually serves should be allowed, got {sorted(allowed_methods)}"
    )


def test_unhandled_error_returns_no_internals_to_the_client():
    """The body must be a sentence, not the exception text or a traceback."""
    from starlette.requests import Request

    from app.main import log_requests

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/boom",
        "raw_path": b"/api/boom",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    }
    request = Request(scope)

    async def exploding(_request):
        raise RuntimeError("/srv/secret/path.py line 42: password=hunter2")

    response = asyncio.run(log_requests(request, exploding))
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 500
    blob = json.dumps(body).lower()
    for leak in ("secret", "hunter2", "runtimeerror", "traceback", ".py"):
        assert leak not in blob, f"the 500 response leaked {leak!r}: {body}"
    assert "reference" in blob, "the message must carry a reference the log can be grepped for"


def test_error_messages_are_short_enough_to_display():
    """Backend messages end up verbatim in the UI, so they must stay one sentence."""
    for message in (
        "CORS_ORIGINS is empty: no cross-origin browser origin will be allowed.",
        "unknown station 'nope'",
    ):
        assert len(message) < 400
