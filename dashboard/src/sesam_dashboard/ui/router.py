"""UI page routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sesam_dashboard.db import (
    fetch_view_checklist,
    fetch_component_gate,
    fetch_pgtrickle_stream_state,
    fetch_source_table_state,
    fetch_migration_history,
    fetch_pipeline_flow_counts,
    fetch_model_overview,
    fetch_webhook_log_state,
    fetch_ingest_schedule,
    fetch_writeback_results,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _status_class(status: str | None) -> str:
    """Map a pgtrickle stream status to a Tailwind badge colour class."""
    return {
        "ok": "bg-green-900 text-green-300",
        "lagging": "bg-amber-900 text-amber-300",
        "error": "bg-red-900 text-red-300",
        "no_events": "bg-slate-700 text-slate-400",
    }.get(str(status or "").lower(), "bg-slate-700 text-slate-400")


templates.env.filters["status_class"] = _status_class


def build_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/ui", response_class=HTMLResponse)
    @router.get("/ui/", response_class=HTMLResponse)
    async def landing(request: Request):
        pool = request.app.state.pool
        mapping = request.app.state.mapping

        (
            view_checklist,
            gate,
            streams,
            sources,
            flow,
            webhooks,
            schedule,
        ) = await _gather(pool, mapping)

        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                "view_checklist": view_checklist,
                "component_gate": gate,
                "streams": streams,
                "sources": sources,
                "flow": flow,
                "webhooks": webhooks,
                "schedule": schedule,
            },
        )

    @router.get("/ui/pipelines", response_class=HTMLResponse)
    async def pipelines(request: Request):
        pool = request.app.state.pool
        mapping = request.app.state.mapping
        flow = await fetch_pipeline_flow_counts(pool, mapping)
        return templates.TemplateResponse(
            request,
            "pipelines.html",
            {"flow": flow},
        )

    @router.get("/ui/model", response_class=HTMLResponse)
    async def model(request: Request):
        pool = request.app.state.pool
        mapping = request.app.state.mapping
        targets = await fetch_model_overview(pool, mapping)
        return templates.TemplateResponse(
            request,
            "model.html",
            {"targets": targets},
        )

    @router.get("/ui/trace", response_class=HTMLResponse)
    async def trace(request: Request):
        query = request.query_params.get("q", "")
        return templates.TemplateResponse(
            request,
            "trace.html",
            {"query": query},
        )

    @router.get("/ui/dag", response_class=HTMLResponse)
    async def dag(request: Request):
        mapping = request.app.state.mapping
        return templates.TemplateResponse(
            request,
            "dag.html",
            {"mapping": mapping},
        )

    @router.get("/ui/writeback", response_class=HTMLResponse)
    async def writeback(request: Request):
        import json
        import datetime

        pool = request.app.state.pool
        data = await fetch_writeback_results(pool, limit=200)

        def _serial(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.isoformat()
            raise TypeError(f"Not serializable: {type(obj)}")

        recent_json = json.dumps(data["recent"], default=_serial)
        return templates.TemplateResponse(
            request,
            "writeback.html",
            {
                "summary": data["summary"],
                "recent": data["recent"],
                "recent_json": recent_json,
            },
        )

    @router.get("/ui/control", response_class=HTMLResponse)
    async def control(request: Request):
        pool = request.app.state.pool
        gate = await fetch_component_gate(pool)
        migrations = await fetch_migration_history(pool)
        schema_manager_url = request.app.state.schema_manager_url
        return templates.TemplateResponse(
            request,
            "control.html",
            {
                "component_gate": gate,
                "migrations": migrations,
                "schema_manager_url": schema_manager_url,
            },
        )

    return router


async def _gather(pool, mapping):
    import asyncio

    results = await asyncio.gather(
        fetch_view_checklist(pool, mapping),
        fetch_component_gate(pool),
        fetch_pgtrickle_stream_state(pool),
        fetch_source_table_state(pool, mapping),
        fetch_pipeline_flow_counts(pool, mapping),
        fetch_webhook_log_state(pool),
        fetch_ingest_schedule(pool),
    )
    return results
