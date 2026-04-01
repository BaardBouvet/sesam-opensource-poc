"""Database query helpers for the Sesam dashboard."""

from __future__ import annotations

from typing import Any

import asyncpg


# ---------------------------------------------------------------------------
# Schema & view state
# ---------------------------------------------------------------------------


async def fetch_view_checklist(pool: asyncpg.Pool, mapping) -> list[dict]:
    """For every OSI view/table expected from mapping.yaml, check exists + validity + row count."""
    expected_views: list[str] = []
    for target_name in mapping.targets:
        for m in mapping.targets[target_name].mappings:
            if not m.parent:  # skip derived mappings — they share a source table
                expected_views.append(f"_fwd_{m.name}")
        expected_views.append(f"_id_{target_name}")
        expected_views.append(f"_resolved_{target_name}")
        expected_views.append(f"_delta_{target_name}")
        expected_views.append(target_name)  # consumer view

    results = []
    async with pool.acquire() as conn:
        existing: set[str] = {
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        for view_name in expected_views:
            exists = view_name in existing
            valid: bool | None = None
            row_count: int | None = None
            error_msg: str | None = None
            if exists:
                try:
                    await conn.execute(f'EXPLAIN SELECT * FROM "{view_name}" LIMIT 0')
                    valid = True
                    row = await conn.fetchrow(
                        f'SELECT COUNT(*) AS n FROM "{view_name}"'
                    )
                    row_count = row["n"]
                except Exception as exc:
                    valid = False
                    error_msg = str(exc)
            results.append(
                dict(
                    view_name=view_name,
                    exists=exists,
                    valid=valid,
                    row_count=row_count,
                    error_msg=error_msg,
                )
            )
    return results


async def fetch_component_gate(pool: asyncpg.Pool) -> list[dict]:
    """Read component_state table — controls whether ingest/writeback are gated."""
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "SELECT component, desired, schema_version, updated_at FROM component_state ORDER BY component"
            )
            return [dict(r) for r in rows]
        except asyncpg.UndefinedTableError:
            return []


async def fetch_pgtrickle_stream_state(pool: asyncpg.Pool) -> list[dict]:
    """Read pgtrickle.quick_health for current IVM stream status."""
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT stream_name, status, lag_bytes, lag_rows, last_event_at
                FROM pgtrickle.quick_health
                ORDER BY
                    CASE status
                        WHEN 'error'     THEN 0
                        WHEN 'lagging'   THEN 1
                        WHEN 'no_events' THEN 2
                        ELSE             3
                    END,
                    stream_name
                """
            )
            return [dict(r) for r in rows]
        except Exception:
            return []


async def fetch_source_table_state(pool: asyncpg.Pool, mapping) -> list[dict]:
    """For each inout_src_* source table, return row count and last _ingested_at."""
    results = []
    async with pool.acquire() as conn:
        existing_tables: set[str] = {
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        pgtrickle_streams: set[str] = set()
        try:
            rows = await conn.fetch("SELECT pgt_name FROM pgtrickle.stream_tables_info")
            pgtrickle_streams = {r["pgt_name"] for r in rows}
        except Exception:
            pass

        seen_tables: set[str] = set()
        for source_name, table_name in mapping.sources.items():
            if table_name in seen_tables:
                continue
            seen_tables.add(table_name)
            exists = table_name in existing_tables
            row_count: int | None = None
            last_ingested: Any = None
            if exists:
                try:
                    r = await conn.fetchrow(
                        f'SELECT COUNT(*) AS n, MAX(_ingested_at) AS last_ingested FROM "{table_name}"'
                    )
                    row_count = r["n"]
                    last_ingested = r["last_ingested"]
                except Exception:
                    pass
            results.append(
                dict(
                    source_name=source_name,
                    table_name=table_name,
                    exists=exists,
                    row_count=row_count,
                    last_ingested=last_ingested,
                    pgtrickle_tracked=table_name in pgtrickle_streams,
                )
            )
    return results


async def fetch_migration_history(pool: asyncpg.Pool) -> list[dict]:
    """Fetch applied migration versions from schema_manager_migrations or alembic_version."""
    async with pool.acquire() as conn:
        for table in ("schema_manager_migrations", "alembic_version"):
            try:
                rows = await conn.fetch(
                    f"SELECT * FROM {table} ORDER BY applied_at DESC LIMIT 20"
                )
                return [dict(r) for r in rows]
            except Exception:
                continue
        return []


async def fetch_webhook_log_state(pool: asyncpg.Pool) -> list[dict]:
    """
    Per (connector, datatype) summary from inout_ops_webhook_log:
      - last_received_at  – timestamp of the most recent event
      - total_count       – total events ever received
      - error_count       – events with status != 'processed'
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT
                    connector,
                    datatype,
                    MAX(received_at)                              AS last_received_at,
                    COUNT(*)                                      AS total_count,
                    COUNT(*) FILTER (WHERE status != 'processed') AS error_count
                FROM inout_ops_webhook_log
                GROUP BY connector, datatype
                ORDER BY connector, datatype
                """
            )
            return [dict(r) for r in rows]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Pipeline data-flow counts
# ---------------------------------------------------------------------------


async def fetch_pipeline_flow_counts(pool: asyncpg.Pool, mapping) -> list[dict]:
    """
    For every (mapping → target) pair, return row counts at each pipeline layer:
    inout_src_* → _fwd_* → _id_* (distinct clusters) → _resolved_* → dst (pending) → lwstate
    """
    results = []

    async with pool.acquire() as conn:

        async def safe_count(query: str) -> int | None:
            try:
                row = await conn.fetchrow(query)
                return row[0]
            except Exception:
                return None

        for target_name, target in mapping.targets.items():
            for m in target.mappings:
                src_count = (
                    await safe_count(f'SELECT COUNT(*) FROM "{m.source_table}"')
                    if m.source_table
                    else None
                )

                fwd_view = f"_fwd_{m.name}"
                fwd_count = await safe_count(f'SELECT COUNT(*) FROM "{fwd_view}"')

                id_view = f"_id_{target_name}"
                cluster_count = await safe_count(
                    f'SELECT COUNT(DISTINCT _entity_id_resolved) FROM "{id_view}"'
                )

                resolved_view = f"_resolved_{target_name}"
                resolved_count = await safe_count(
                    f'SELECT COUNT(*) FROM "{resolved_view}"'
                )

                pending_count: int | None = None
                written_count: int | None = None
                if m.written_state:
                    dst_table = m.written_state.table.replace("_lwstate", "")
                    pending_count = await safe_count(
                        f"""SELECT COUNT(*) FROM "{dst_table}"
                            WHERE _action IS NOT NULL AND _action != 'noop'"""
                    )
                    written_count = await safe_count(
                        f'SELECT COUNT(*) FROM "{m.written_state.table}"'
                    )

                results.append(
                    dict(
                        mapping_name=m.name,
                        target=target_name,
                        source_table=m.source_table,
                        src_count=src_count,
                        fwd_count=fwd_count,
                        cluster_count=cluster_count,
                        resolved_count=resolved_count,
                        pending_count=pending_count,
                        written_count=written_count,
                        written_state_table=m.written_state.table
                        if m.written_state
                        else None,
                    )
                )
    return results


# ---------------------------------------------------------------------------
# Model overview
# ---------------------------------------------------------------------------


async def fetch_model_overview(pool: asyncpg.Pool, mapping) -> list[dict]:
    """
    For each target, return entity counts and cluster-size distribution
    from _id_{target} and _resolved_{target}.
    """
    results = []

    async with pool.acquire() as conn:

        async def safe_fetchval(query: str):
            try:
                return await conn.fetchval(query)
            except Exception:
                return None

        async def safe_fetch(query: str):
            try:
                return [dict(r) for r in await conn.fetch(query)]
            except Exception:
                return []

        for target_name in mapping.targets:
            id_view = f"_id_{target_name}"
            resolved_view = f"_resolved_{target_name}"

            resolved_count = await safe_fetchval(
                f'SELECT COUNT(*) FROM "{resolved_view}"'
            )
            cluster_count = await safe_fetchval(
                f'SELECT COUNT(DISTINCT _entity_id_resolved) FROM "{id_view}"'
            )
            merged_count = await safe_fetchval(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT _entity_id_resolved
                    FROM "{id_view}"
                    GROUP BY _entity_id_resolved
                    HAVING COUNT(DISTINCT _mapping) > 1
                ) t
                """
            )
            cluster_distribution = await safe_fetch(
                f"""
                SELECT cluster_size, COUNT(*) AS clusters
                FROM (
                    SELECT _entity_id_resolved, COUNT(DISTINCT _mapping) AS cluster_size
                    FROM "{id_view}"
                    GROUP BY _entity_id_resolved
                ) t
                GROUP BY cluster_size
                ORDER BY cluster_size
                """
            )

            results.append(
                dict(
                    target=target_name,
                    resolved_count=resolved_count,
                    cluster_count=cluster_count,
                    merged_count=merged_count,
                    cluster_distribution=cluster_distribution,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Entity discovery (for trace page search)
# ---------------------------------------------------------------------------


async def fetch_entity_samples(
    pool: asyncpg.Pool,
    mapping,
    q: str = "",
    limit: int = 20,
) -> list[dict]:
    """
    Search source tables and resolved views for entities matching *q*.

    Each hit has:
      - record_id   – raw source record_id (present for source hits)
      - entity_id   – _entity_id_resolved (present for resolved hits)
      - name        – best-effort display name from data->>'name'
      - source      – human label (e.g. "hubspot_companies", "_resolved_contacts")
    """
    results: list[dict] = []
    q_lower = q.strip().lower()

    async with pool.acquire() as conn:

        async def safe_fetch(query: str, *args) -> list[asyncpg.Record]:
            try:
                return await conn.fetch(query, *args)
            except Exception:
                return []

        seen_tables: set[str] = set()
        for source_name, table_name in mapping.sources.items():
            if table_name in seen_tables:
                continue
            seen_tables.add(table_name)

            if q_lower:
                rows = await safe_fetch(
                    f"""
                    SELECT record_id::text AS record_id,
                           COALESCE(data->>'name', data->>'fullName', '') AS name
                    FROM "{table_name}"
                    WHERE record_id::text ILIKE $1
                       OR COALESCE(data->>'name', data->>'fullName', '') ILIKE $1
                    ORDER BY record_id
                    LIMIT $2
                    """,
                    f"%{q_lower}%",
                    limit,
                )
            else:
                rows = await safe_fetch(
                    f"""
                    SELECT record_id::text AS record_id,
                           COALESCE(data->>'name', data->>'fullName', '') AS name
                    FROM "{table_name}"
                    ORDER BY _ingested_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            for r in rows:
                results.append(
                    dict(
                        record_id=r["record_id"],
                        entity_id=None,
                        name=r["name"] or r["record_id"],
                        source=source_name,
                    )
                )

        for target_name in mapping.targets:
            resolved_view = f"_resolved_{target_name}"
            if q_lower:
                rows = await safe_fetch(
                    f"""
                    SELECT _entity_id_resolved::text AS entity_id,
                           COALESCE(data->>'name', data->>'fullName', '') AS name
                    FROM "{resolved_view}"
                    WHERE _entity_id_resolved::text ILIKE $1
                       OR COALESCE(data->>'name', data->>'fullName', '') ILIKE $1
                    ORDER BY _entity_id_resolved
                    LIMIT $2
                    """,
                    f"%{q_lower}%",
                    limit,
                )
            else:
                rows = await safe_fetch(
                    f"""
                    SELECT _entity_id_resolved::text AS entity_id,
                           COALESCE(data->>'name', data->>'fullName', '') AS name
                    FROM "{resolved_view}"
                    ORDER BY _entity_id_resolved
                    LIMIT $1
                    """,
                    limit,
                )
            for r in rows:
                results.append(
                    dict(
                        record_id=None,
                        entity_id=r["entity_id"],
                        name=r["name"] or r["entity_id"],
                        source=f"_resolved_{target_name}",
                    )
                )

    return results[:limit]
