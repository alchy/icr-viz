"""FastAPI entry-point for the ICR Piano Spectral Editor backend.

Wires logging, schema init, CORS, and routers. Run in dev with:

    uvicorn piano_web.main:app --reload --port 8000

Production (single-process, single-user local tool):

    uvicorn piano_web.main:app --port 8000 --host 127.0.0.1
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .db import init_schema
from .logging_config import configure_logging
from .routers import (
    anchors,
    banks,
    export,
    icr,
    integrity,
    math_analysis,
    midi,
    ops,
    settings as settings_router,
    spline_transfer,
    surface,
    tone_ops,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = logging.getLogger(__name__)
    await init_schema()
    logger.info("app.startup")
    yield
    # Stop child ICR engine on shutdown so the wrapper's Ctrl+C doesn't
    # leak an orphaned icr.exe / icrgui.exe process.
    try:
        from .routers.icr import get_process
        st = get_process().stop()
        if st.return_code is not None or st.pid is None:
            logger.info("app.shutdown.icr_stopped", extra={"return_code": st.return_code})
    except Exception as exc:
        logger.warning("app.shutdown.icr_stop_failed", extra={"detail": str(exc)})
    # Tear down the CPU-bound worker pool so queued scipy work is cancelled
    # and the process exits promptly.
    try:
        from .workers import shutdown_pool
        shutdown_pool()
    except Exception as exc:
        logger.warning("app.shutdown.pool_failed", extra={"detail": str(exc)})
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ICR Piano Spectral Editor — API",
        version="0.1.0",
        description="Backend for the ICR bank editor. Read-only endpoints in i1.",
        lifespan=lifespan,
    )

    # Dev CORS — Vite at :3000 needs cross-origin calls to the :8000 API.
    # In prod (Tauri/pywebview bundle) the frontend is same-origin, so CORS is a no-op.
    cors_origins_env = os.environ.get("ICR_VIZ_CORS_ORIGINS", "http://localhost:3000")
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(banks.router)
    app.include_router(anchors.router)
    app.include_router(ops.router)
    app.include_router(tone_ops.router)
    app.include_router(spline_transfer.router)
    app.include_router(math_analysis.router)
    app.include_router(integrity.router)
    app.include_router(export.router)
    app.include_router(surface.router)
    app.include_router(midi.router)
    app.include_router(icr.router)
    app.include_router(settings_router.router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """Bare-host hits land on the Swagger UI until apps/web is served here."""
        return RedirectResponse(url="/docs", status_code=307)

    @app.get("/api", tags=["meta"])
    async def api_index() -> dict[str, object]:
        """Plain-text-ish route index for curl/debugging."""
        return {
            "name": "ICR Piano Spectral Editor API",
            "version": app.version,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "routes": [
                "GET    /api/health",
                "GET    /api/banks",
                "GET    /api/banks/{bank_id}",
                "GET    /api/banks/{bank_id}/notes",
                "GET    /api/banks/{bank_id}/notes/{midi}/{velocity}",
                "GET    /api/banks/{bank_id}/notes/{midi}/{velocity}/curves",
                "GET    /api/banks/{bank_id}/notes/{midi}/{velocity}/anchors",
                "POST   /api/banks/{bank_id}/notes/{midi}/{velocity}/anchors",
                "PATCH  /api/banks/{bank_id}/anchors/{anchor_id}",
                "DELETE /api/banks/{bank_id}/anchors/{anchor_id}",
                "POST   /api/ops/anchor-interpolate?bank_id=<id>",
                "POST   /api/ops/tone-identify-only?bank_id=<id>",
                "POST   /api/ops/tone-identify-and-correct?bank_id=<id>",
                "POST   /api/ops/spline-transfer?bank_id=<id>",
                "GET    /api/banks/{bank_id}/deviation-report?ref=<id>&min_z=2",
                "GET    /api/banks/{bank_id}/math-analysis",
                "GET    /api/banks/{bank_id}/notes/{midi}/{velocity}/physical-fit",
                "GET    /api/banks/{bank_id}/cross-note/{parameter}/{velocity}/{k}",
                "POST   /api/ops/bank-integrity-validate?bank_id=<id>",
                "GET    /api/banks/{bank_id}/export?format=icr|synth_csv|analysis_csv|ndjson",
                "GET    /api/banks/{bank_id}/surface?parameter&velocity&color_by&difference_from",
                "GET    /api/midi/ports",
                "GET    /api/midi/status",
                "POST   /api/midi/connect",
                "POST   /api/midi/disconnect",
                "POST   /api/midi/play-note",
                "POST   /api/midi/ping",
                "POST   /api/midi/push-bank",
                "POST   /api/midi/push-partial",
                "POST   /api/midi/push-note-param",
                "GET    /api/icr/settings",
                "POST   /api/icr/settings",
                "GET    /api/icr/status",
                "POST   /api/icr/launch",
                "POST   /api/icr/stop",
                "GET    /api/settings",
                "POST   /api/settings",
                "GET    /api/settings/schema",
            ],
        }

    return app


app = create_app()
