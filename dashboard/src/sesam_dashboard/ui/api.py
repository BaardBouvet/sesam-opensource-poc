"""JSON API routes consumed by dashboard JS."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sesam_dashboard.db import (
    fetch_view_checklist,
    fetch_component_gate,
    fetch_pgtrickle_stream_state,
    fetch_source_table_state,
    fetch_migration_history,
    fetch_pipeline_flow_counts,
)


def _jsonable(obj):
    """Recursively convert non-JSON-serialisable types (datetime, etc.)."""
    import datetime

    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(i) for i in obj]
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return obj


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/schema")
    async def api_schema(request: Request):
        pool = request.app.state.pool
        mapping = request.app.state.mapping
        import asyncio

        views, gate, streams, sources, migrations = await asyncio.gather(
            fetch_view_checklist(pool, mapping),
            fetch_component_gate(pool),
            fetch_pgtrickle_stream_state(pool),
            fetch_source_table_state(pool, mapping),
            fetch_migration_history(pool),
        )
        return JSONResponse(
            _jsonable(
                {
                    "views": views,
                    "component_gate": gate,
                    "pgtrickle_streams": streams,
                    "sources": sources,
                    "migrations": migrations,
                }
            )
        )

    @router.get("/pipelines")
    async def api_pipelines(request: Request):
        pool = request.app.state.pool
        mapping = request.app.state.mapping
        flow = await fetch_pipeline_flow_counts(pool, mapping)
        return JSONResponse(_jsonable({"flow": flow}))

    return router
