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
                expected_views.append(f"_delta_{m.name}")
        expected_views.append(f"_id_{target_name}")
        expected_views.append(f"_resolved_{target_name}")
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


async def fetch_view_rows(pool: asyncpg.Pool, view_name: str, limit: int = 50) -> dict:
    """
    Return the first *limit* rows from *view_name* (public schema only).

    Returns {"columns": [...], "rows": [[...], ...]} or raises ValueError if
    the view does not exist in the public schema.
    """
    import re
    import datetime
    import decimal
    import uuid

    if not re.fullmatch(r"[A-Za-z0-9_]+", view_name):
        raise ValueError(f"Invalid view name: {view_name!r}")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = $1",
            view_name,
        )
        if not exists:
            raise ValueError(f"View not found in public schema: {view_name!r}")
        rows = await conn.fetch(f'SELECT * FROM "{view_name}" LIMIT $1', limit)
    if not rows:
        return {"columns": [], "rows": []}

    def _safe(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
            return v.isoformat()
        if isinstance(v, decimal.Decimal):
            return float(v)
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, (bytes, bytearray, memoryview)):
            return v.hex() if not isinstance(v, memoryview) else bytes(v).hex()
        if isinstance(v, dict):
            return {k: _safe(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            return [_safe(i) for i in v]
        return str(v)

    columns = list(rows[0].keys())
    return {
        "columns": columns,
        "rows": [[_safe(v) for v in row.values()] for row in rows],
    }


async def fetch_ingest_schedule(pool: asyncpg.Pool) -> list[dict]:
    """
    For each connector+datatype that has ever synced, return:
      - last sync time, status, record counts, error
      - current watermark (incremental cursor)
      - empirical poll interval (seconds between last two runs)
      - estimated next_poll_at (last_finished_at + interval)
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT
                        connector, datatype,
                        started_at, finished_at, status,
                        records_fetched, records_inserted, records_updated, records_deleted,
                        error_message,
                        ROW_NUMBER() OVER (
                            PARTITION BY connector, datatype
                            ORDER BY started_at DESC
                        ) AS rn
                    FROM inout_ops_sync_run
                    WHERE status IN ('completed', 'skipped', 'failed')
                ),
                latest AS (SELECT * FROM ranked WHERE rn = 1),
                prev   AS (
                    SELECT connector, datatype, started_at AS prev_started_at
                    FROM ranked WHERE rn = 2
                ),
                wm AS (
                    SELECT connector, datatype, watermark_value, updated_at AS watermark_updated_at
                    FROM inout_ops_watermark
                )
                SELECT
                    l.connector,
                    l.datatype,
                    l.started_at        AS last_started_at,
                    l.finished_at       AS last_finished_at,
                    l.status            AS last_status,
                    l.records_fetched,
                    l.records_inserted,
                    l.records_updated,
                    l.records_deleted,
                    l.error_message,
                    w.watermark_value,
                    w.watermark_updated_at,
                    CASE WHEN p.prev_started_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (l.started_at - p.prev_started_at))::int
                    END AS interval_seconds,
                    CASE WHEN p.prev_started_at IS NOT NULL AND l.finished_at IS NOT NULL
                        THEN l.finished_at + (l.started_at - p.prev_started_at)
                    END AS next_poll_at
                FROM latest l
                LEFT JOIN prev p USING (connector, datatype)
                LEFT JOIN wm   w USING (connector, datatype)
                ORDER BY l.connector, l.datatype
                """
            )
            return [dict(r) for r in rows]
        except Exception:
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
                dst_table: str | None = None
                # Pending writes: count non-noop rows from the OSI-mapping delta view.
                # The writeback engine reads from _delta_{mapping} by default, not
                # inout_dst_* (desired-state table), so we query the same source.
                delta_view = f"_delta_{m.name}"
                pending_count = await safe_count(
                    f"""SELECT COUNT(*) FROM "{delta_view}" WHERE _action != 'noop'"""
                )
                if m.written_state:
                    dst_table = delta_view  # used for "pending write" click-through
                    written_count = await safe_count(
                        f'SELECT COUNT(*) FROM "{m.written_state.table}"'
                    )

                results.append(
                    dict(
                        mapping_name=m.name,
                        target=target_name,
                        source_table=m.source_table,
                        fwd_table=fwd_view,
                        id_table=id_view,
                        resolved_table=resolved_view,
                        dst_table=dst_table,
                        written_state_table=m.written_state.table
                        if m.written_state
                        else None,
                        src_count=src_count,
                        fwd_count=fwd_count,
                        cluster_count=cluster_count,
                        resolved_count=resolved_count,
                        pending_count=pending_count,
                        written_count=written_count,
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


async def fetch_writeback_results(pool: asyncpg.Pool, limit: int = 100) -> dict:
    """
    Return writeback audit data from inout_ops_writeback_result.

    Returns {"summary": [...], "recent": [...]} where:
    - summary: per (connector, datatype) aggregate counts + last error
    - recent: last *limit* rows with all columns for inline inspection
    """
    async with pool.acquire() as conn:
        try:
            summary_rows = await conn.fetch(
                """
                SELECT
                    connector,
                    datatype,
                    COUNT(*)                                                       AS total,
                    COUNT(*) FILTER (WHERE status = 'ok')                          AS ok_count,
                    COUNT(*) FILTER (WHERE status = 'failed')                      AS failed_count,
                    MAX(processed_at)                                              AS last_processed_at,
                    MAX(processed_at) FILTER (WHERE status = 'failed')             AS last_failure_at,
                    (ARRAY_AGG(error_message ORDER BY processed_at DESC)
                        FILTER (WHERE status = 'failed')
                    )[1]                                                            AS last_error_message
                FROM inout_ops_writeback_result
                GROUP BY connector, datatype
                ORDER BY last_processed_at DESC NULLS LAST
                """
            )
            recent_rows = await conn.fetch(
                """
                SELECT
                    id,
                    connector,
                    datatype,
                    action,
                    external_id,
                    status,
                    error_message,
                    response_status,
                    field_diff,
                    response_body,
                    payload_snapshot,
                    protection_level,
                    run_id::text AS run_id,
                    processed_at
                FROM inout_ops_writeback_result
                ORDER BY processed_at DESC
                LIMIT $1
                """,
                limit,
            )
            return {
                "summary": [dict(r) for r in summary_rows],
                "recent": [dict(r) for r in recent_rows],
            }
        except Exception:
            return {"summary": [], "recent": []}


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
