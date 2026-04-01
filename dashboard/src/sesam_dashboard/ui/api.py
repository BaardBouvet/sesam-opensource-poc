"""JSON API routes consumed by dashboard JS."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from fastapi import HTTPException

from sesam_dashboard.db import (
    fetch_view_checklist,
    fetch_component_gate,
    fetch_pgtrickle_stream_state,
    fetch_source_table_state,
    fetch_migration_history,
    fetch_pipeline_flow_counts,
    fetch_entity_samples,
    fetch_view_rows,
    fetch_writeback_results,
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

    @router.get("/entities")
    async def api_entities(
        request: Request,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        pool = request.app.state.pool
        mapping = request.app.state.mapping
        hits = await fetch_entity_samples(pool, mapping, q=q, limit=limit)
        return JSONResponse(_jsonable({"results": hits}))

    @router.get("/view/{view_name}/rows")
    async def api_view_rows(
        request: Request,
        view_name: str,
        limit: int = Query(default=50, ge=1, le=500),
    ):
        pool = request.app.state.pool
        try:
            data = await fetch_view_rows(pool, view_name, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(_jsonable(data))

    @router.get("/writeback")
    async def api_writeback(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        pool = request.app.state.pool
        data = await fetch_writeback_results(pool, limit=limit)
        return JSONResponse(_jsonable(data))

    @router.post("/reconcile")
    async def api_reconcile(request: Request):
        import httpx

        schema_manager_url = request.app.state.schema_manager_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{schema_manager_url}/reconcile")
            return JSONResponse(
                {"status": "ok", "code": resp.status_code, "body": resp.text},
                status_code=resp.status_code,
            )
        except Exception as exc:
            return JSONResponse(
                {"status": "error", "detail": str(exc)}, status_code=502
            )

    return router
