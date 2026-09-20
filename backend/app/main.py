"""Factory Time Machine - FastAPI application.

Run with:

    cd backend && uvicorn app.main:app --reload --port 8000

On startup the dataset catalog is built in a background thread; poll
``/api/datasets/status`` for progress. The system works with or without an AI API
key - see ``/api/ai/status``.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, ensure_dirs, settings
from .db import init_db
from .json_utils import NumpySafeJSONResponse
from .logging_setup import setup_logging
from .routers import analytics, datasets, external_images, inspection, ops, simulation, upload, workspace
from .services.catalog import catalog

log = logging.getLogger("ftm.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    ensure_dirs()
    init_db()
    if settings.ai_enabled:
        log.info("AI API enabled: provider=%s model=%s", settings.ai_provider, settings.ai_model)
    else:
        log.info("AI API not configured - deterministic narrative engine will be used")
    catalog.start_background_build()
    yield
    log.info("shutting down")


app = FastAPI(
    title="Factory Time Machine",
    description=(
        "Manufacturing forensic decision support: inspection, process anomaly analysis, route-ordered "
        "divergence, failure propagation, bottleneck analysis, gated economics, what-if simulation and "
        "human-in-the-loop review. Advisory only - it controls no equipment."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # The analytics compute in numpy; the default encoder refuses numpy scalars,
    # which silently turned successful results into 500s.
    default_response_class=NumpySafeJSONResponse,
)

if not settings.cors_origins:
    # An empty CORS_ORIGINS used to fall through to ["*"], i.e. a blank setting
    # silently widened the API to every website. Empty now means "no cross-origin
    # browser access", which is the safe reading of a blank list.
    log.warning(
        "CORS_ORIGINS is empty: no cross-origin browser origin will be allowed. "
        "Set it explicitly (e.g. http://localhost:5173) to use the Vite dev server."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # pragma: no cover - defensive
        # The full traceback goes to the log; the client gets a plain sentence.
        # Returning the exception text leaked internal paths and library internals
        # to whoever triggered the error.
        reference = uuid.uuid4().hex[:8]
        log.exception("unhandled error on %s %s (reference %s)", request.method, request.url.path, reference)
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Something went wrong while handling that request. It has been logged as reference "
                    f"{reference}; please try again or check the backend log."
                )
            },
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not request.url.path.startswith(("/api/inspection/image", "/api/inspection/thumbnail")):
        level = logging.WARNING if response.status_code >= 400 else logging.DEBUG
        log.log(level, "%s %s -> %d in %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.0f}"
    return response


app.include_router(datasets.router)
app.include_router(analytics.router)
app.include_router(inspection.router)
app.include_router(external_images.router)
app.include_router(simulation.router)
app.include_router(ops.router)
app.include_router(upload.router)
# Dataset case files: registry/history, saved analysis, per-dataset rate cards,
# repair options, report downloads and the active-dataset preference.
app.include_router(workspace.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "catalog": catalog.status.snapshot(),
        "ai": {"enabled": settings.ai_enabled, "provider": settings.ai_provider},
        "advisory_only": True,
    }


# ---------------------------------------------------------------------------
# Serve the built frontend when it exists (single-origin deployment).
# ---------------------------------------------------------------------------
DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(DIST / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "service": "Factory Time Machine API",
            "docs": "/docs",
            "frontend": "not built - run `cd frontend && npm install && npm run dev`",
        }
