"""Application factory: wires config, DB, engine, workers, and middleware."""

from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, metrics
from .api import router
from .auth import RateLimiter, ensure_bootstrap_key
from .config import Config, load_config
from .db import Database
from .engine import GalleryDLEngine
from .events import EventHub
from .logging_setup import setup_logging
from .models import JobState
from .worker import WorkerPool

log = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'"
    ),
}


def create_app(cfg: Config | None = None, engine=None) -> FastAPI:
    cfg = cfg or load_config()
    setup_logging(cfg.logging, cfg.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(cfg.db_path)
        await db.open()
        await ensure_bootstrap_key(db)
        hub = EventHub()
        eng = engine or GalleryDLEngine()
        workers = WorkerPool(cfg, db, eng, hub)

        app.state.cfg = cfg
        app.state.db = db
        app.state.engine = eng
        app.state.hub = hub
        app.state.workers = workers
        app.state.rate_limiter = RateLimiter()

        await workers.start()
        log.info("grabbit %s started (engine channel: %s)", __version__, cfg.engine.channel)
        yield
        await workers.stop()
        await db.close()

    app = FastAPI(
        title="Grabbit",
        version=__version__,
        root_path=cfg.server.root_path,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(router)

    trusted = [ipaddress.ip_network(p, strict=False) for p in cfg.server.trusted_proxies]

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        # Honor X-Forwarded-* only from configured proxies (uvicorn's
        # proxy-headers middleware handles the rewrite; here we just guard).
        client = request.client.host if request.client else ""
        try:
            from_trusted_proxy = any(
                ipaddress.ip_address(client) in net for net in trusted)
        except ValueError:
            from_trusted_proxy = False
        if not from_trusted_proxy:
            for h in ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"):
                if h in request.headers:
                    # Untrusted client sent proxy headers: scope them out by
                    # rebuilding the header list without them.
                    request.scope["headers"] = [
                        (k, v) for k, v in request.scope["headers"]
                        if k.decode().lower() not in
                        ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host")
                    ]
                    break
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    if cfg.metrics.enabled:
        @app.get("/metrics")
        async def prometheus_metrics(request: Request) -> Response:
            counts = await request.app.state.db.stats()
            metrics.queue_depth.set(counts.get(JobState.QUEUED.value, 0))
            metrics.jobs_active.set(counts.get(JobState.ACTIVE.value, 0))
            body, content_type = metrics.render()
            return Response(content=body, media_type=content_type)

    # Static SPA (built by M2); mounted last so /api and /metrics win.
    ui_dir = Path(__file__).parent / "static"
    if ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

    return app
