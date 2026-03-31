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

        view_checklist, gate, streams, sources, flow = await _gather(pool, mapping)

        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                "view_checklist": view_checklist,
                "component_gate": gate,
                "streams": streams,
                "sources": sources,
                "flow": flow,
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
    )
    return results
