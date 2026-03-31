"""Sesam Dashboard — CLI entry point."""

from __future__ import annotations

import typer
import uvicorn
from rich.console import Console

app = typer.Typer(
    name="sesam-dashboard",
    help="Operational dashboard for the Sesam OSS PoC pipeline.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def main(
    database_url: str = typer.Option(
        ...,
        "--database-url",
        envvar="DASHBOARD_DATABASE_URL",
        help="asyncpg-compatible PostgreSQL DSN.",
    ),
    mapping_file: str = typer.Option(
        "/config/mapping.yaml",
        "--mapping-file",
        envvar="DASHBOARD_MAPPING_FILE",
        help="Path to mapping.yaml.",
    ),
    ingest_url: str = typer.Option(
        "http://inandout-ingest:9090",
        "--ingest-url",
        envvar="DASHBOARD_INGEST_URL",
    ),
    schema_manager_url: str = typer.Option(
        "http://inandout-schema-manager:9080",
        "--schema-manager-url",
        envvar="DASHBOARD_SCHEMA_MANAGER_URL",
    ),
    listen: str = typer.Option(
        "0.0.0.0:8888",
        "--listen",
        envvar="DASHBOARD_LISTEN",
    ),
) -> None:
    from sesam_dashboard.app import create_app

    host, port_str = listen.rsplit(":", 1)
    application = create_app(
        database_url=database_url,
        mapping_file=mapping_file,
        ingest_url=ingest_url,
        schema_manager_url=schema_manager_url,
    )
    console.print(f"[bold green]sesam-dashboard[/] listening on {listen}")
    uvicorn.run(application, host=host, port=int(port_str), log_level="info")
