"""FastAPI application factory for the Sesam dashboard."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
from prometheus_client import make_asgi_app as prometheus_make_asgi_app

from sesam_dashboard.mapping_reader import load_mapping
from sesam_dashboard.metrics import (
    REGISTRY,
    dashboard_requests_total,
    dashboard_request_duration_seconds,
)
from sesam_dashboard.ui.router import build_ui_router
from sesam_dashboard.ui.api import build_api_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await asyncpg.create_pool(
        app.state.database_url,
        min_size=1,
        max_size=5,
        server_settings={"application_name": "sesam-dashboard"},
    )
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


def create_app(
    database_url: str,
    mapping_file: str,
    ingest_url: str,
    schema_manager_url: str,
) -> FastAPI:
    app = FastAPI(title="Sesam Dashboard", docs_url=None, redoc_url=None)
    app.state.database_url = database_url
    app.state.mapping = load_mapping(mapping_file)
    app.state.ingest_url = ingest_url
    app.state.schema_manager_url = schema_manager_url

    app.router.lifespan_context = _lifespan

    _static = Path(__file__).parent / "ui" / "static"
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

    app.include_router(build_api_router())
    app.include_router(build_ui_router())

    @app.middleware("http")
    async def _record_metrics(request: Request, call_next):
        # Collapse path parameters into a stable label to avoid high cardinality.
        path = request.url.path
        method = request.method
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        dashboard_requests_total.labels(
            method=method, path=path, status_code=str(response.status_code)
        ).inc()
        dashboard_request_duration_seconds.labels(method=method, path=path).observe(
            duration
        )
        return response

    app.mount("/metrics", prometheus_make_asgi_app(registry=REGISTRY))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def root():
        return RedirectResponse("/ui")

    return app
