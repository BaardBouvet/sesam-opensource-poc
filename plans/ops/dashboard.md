# Sesam Dashboard — Operational UI

Status: Draft
Date: 2026-03-31

## Context

The cluster runs several moving parts — ingestion daemons, writeback daemons, the OSI-mapping view layer,
pg-trickle IVM, and the schema-manager — but there is no human-facing UI to inspect what is happening
or to trigger operations without `kubectl exec` or raw SQL. The simulator already ships a FastAPI + Jinja2
dashboard for its own state; this plan describes a dedicated `sesam-dashboard` service built with the
same stack that exposes the internals of the *pipeline* rather than the simulator.

## What Has To Be True Before We Build

- The ingest engine's `/connectors` API (port 9090) and the `inout_ops_sync_run` table exist and are
  populated. Both are already true. See [data-ingestion.md](../ingest/data-ingestion.md).
- The schema-manager `/reconcile` endpoint (port 9080) is live. Already true.

## Service Overview

| Property | Value |
|---|---|
| Service name | `sesam-dashboard` |
| Port | **8888** (avoids clashes with ingest 9090, schema-manager 9080, simulator 6100) |
| Language | Python 3.13 |
| Framework | FastAPI + Uvicorn (same as simulator) |
| Templates | Jinja2 (same as simulator) |
| Live updates | Server-Sent Events (same as simulator) |
| Data sources | PostgreSQL (direct), ingest engine HTTP API, schema-manager HTTP API |
| Location | `dashboard/` at project root (project-specific; not a reusable vendor package) |

## Scope boundary

Process health (is ingest running, CPU/memory, error rates over time, alerts) belongs in Grafana.
This dashboard covers things that do not make sense in Grafana: structural state of the data model,
data-level counts at each pipeline layer, entity-level merge inspection, and operational controls
that act on data rather than infrastructure.

## Page Priority

| Priority | Page | Answers |
|---|---|---|
| 1 | `/ui` — Schema & Pipeline State | Did the views get created? Is data flowing through each layer? |
| 2 | `/ui/sync-status/{target}` — Sync Matrix | Where is each entity across connected systems? |
| 3 | `/ui/model` — Golden Record Explorer | What did the merge produce? |
| 4 | `/ui/trace` — Entity Tracer | Follow one entity end-to-end |
| 5 | `/ui/dag` — Mapping DAG | Visual overview of the mapping structure |
| 6 | `/ui/control` — Actions Panel | Force-sync, reconcile, requeue |

## Pages

### `/ui` — Schema & Pipeline State (landing page)

Combines two questions always asked together when something is wrong: "did the schema-manager create
the right views?" and "is data actually flowing through them?" Neither belongs in Grafana — they are
structural snapshots, not time-series metrics.

### `/ui/schema` — Schema & View State

#### OSI mapping view checklist

For every view name expected from `mapping.yaml` (`_fwd_*`, `_id_*`, `_resolved_*`, `_delta_*`,
and the consumer view), show:

| View | Exists | Valid | Row count | Last DDL |
|---|---|---|---|---|
| `_fwd_hubspot_contacts` | ✓ | ✓ | 42 | 2 min ago |
| `_id_person` | ✓ | ✓ | 42 | 2 min ago |
| `_resolved_person` | ✓ | ✗ | ERROR | 2 min ago |
| `person` | — | — | — | — |

**Exists**: `SELECT 1 FROM information_schema.views WHERE table_name = ?`
**Valid**: `EXPLAIN SELECT * FROM {view} LIMIT 0` — catches broken view SQL without a full scan
**Row count**: `SELECT COUNT(*) FROM {view}` — only run if valid

A broken view shows the full Postgres error message inline so the developer doesn't have to `psql`
to diagnose it.

#### Component gate state

The `component_state` table is schema-manager state, not process health — it controls whether ingest
and writeback are allowed to process rows at all. Show it here rather than in a health strip:

```sql
SELECT component, desired, schema_version, updated_at
FROM component_state;
```

If `desired != 'running'`, display a prominent banner explaining that the schema-manager hasn't
released the gate yet. This was the single most confusing failure mode: ingest appeared healthy
but was silently doing nothing.

#### pg-trickle stream state

Not a metric — a structural question: "is this IVM view correctly tracking its source table?":

```sql
SELECT stream_name, status, lag_rows, last_event_at
FROM pgtrickle.quick_health
ORDER BY status DESC, stream_name;
```

`status = 'ok'` → green, `'lagging'` → amber, `'error'` → red, `'no_events'` → grey.
A `no_events` stream for `_resolved_person` means the OSI mapping IVM has never received a change —
typically because the `_id_person` view it depends on was recreated without a pgtrickle re-registration.

#### Source table checklist

For all `inout_src_*` tables: exists, row count, last `_ingested_at`, whether pg-trickle is
tracking it (join to `pgtrickle.stream_tables_info`).

#### Schema-manager reconcile log

Last 10 reconcile events: timestamp, outcome (ok/error), what changed (DDL statements applied).
If the schema-manager doesn't expose this yet, log it to a dashboard-owned `dashboard_reconcile_log`
table on each manual trigger.

#### Migration history

```sql
SELECT version, applied_at, description
FROM schema_manager_migrations  -- or alembic_version
ORDER BY applied_at DESC;
```

This would have immediately shown that the grant migrations were failing silently.

### `/ui/pipelines` — Pipeline Overview

One row per connector × datatype pair. The central question is not "did a sync run" (Grafana
alerts on that) but **"how many records made it through each layer?"** — a structural snapshot that
immediately shows where the pipeline is dropping records.

**Data flow counts** — the query we have run most often as raw SQL. For each
datatype, show counts at every layer so it's immediately obvious where records are dropping:

| Stage | Table/View | Count |
|---|---|---|
| Ingested | `inout_src_{c}_{d}` | 42 |
| Forwarded | `_fwd_{mapping}` | 42 |
| In cluster | `_id_{target}` (distinct _entity_id_resolved) | 35 |
| Resolved | `_resolved_{target}` | 35 |
| Pending write | `inout_dst_{c}_{d}` where `_action != 'noop'` | 2 |
| Written | `inout_dst_{c}_{d}_lwstate` | 33 |

A drop between any two rows points directly to the broken layer.

Also show per-datatype: last sync watermark (from `inout_ops_watermark`), circuit-breaker state
(from ingest engine `/connectors` API), and a **Force-sync** button. These are control-plane items
that don't belong in Grafana.

Drills into one connector + datatype. Two sections:

**Recent sync runs** — last 20 rows from `inout_ops_sync_run` for this datatype, shown as a timeline
table with status badge, started_at, duration, records_upserted, records_deleted, error_message.

**DAG stages** — the chain of PostgreSQL views that produce the golden record for this datatype, read
from `mapping.yaml` + the view definitions discovered via `information_schema.views`. Each stage shows:
- View name
- Row count (live `SELECT COUNT(*)`)
- Last DDL timestamp (from `information_schema.views` or `pg_stat_user_tables`)
- Whether the view is currently valid (try a `EXPLAIN` probe; mark invalid in red)

For company and person targets the stages are roughly:
```
inout_src_{connector}_{datatype}
  └─ osi_cdm_{target}_{connector}_normalized   (per-source normalisation view)
       └─ osi_cdm_{target}_resolved            (identity resolution, coalesce)
            └─ osi_cdm_{target}                (golden record)
```
The actual view chain is discovered at runtime from the DB rather than hard-coded.

### `/ui/model` — Golden Record Explorer

The model layer is what makes the system interesting — this page exposes what lives in the middle of
the pipeline. Three cards, one per canonical target (`person`, `company`, `person_company_association`).
Each card shows:

- Entity count (from `SELECT COUNT(*) FROM _resolved_{target}` or the consumer view)
- Merged-cluster count vs single-source count (clusters where `COUNT(DISTINCT _mapping) > 1` in `_id_{target}`)
- Link to browse individual entities

Query pattern used throughout this page:
```sql
-- cluster size distribution
SELECT COUNT(*) AS clusters, cluster_size
FROM (
    SELECT _entity_id_resolved, COUNT(DISTINCT _mapping) AS cluster_size
    FROM _id_person
    GROUP BY _entity_id_resolved
) t
GROUP BY cluster_size ORDER BY cluster_size;
```

### `/ui/model/{target}` — Entity List

Paginated table of all resolved entities for a target (e.g. `/ui/model/person`).

Columns:
- Entity ID (first 8 chars, linked to detail page)
- Key identity field(s) — `email` for person, `org_number` / `domain` for company
- Name / primary display field
- **Sources** — badge per contributing mapping (e.g. `hubspot_contacts` `tripletex_contacts`)
- Merge status — `merged` (2+ sources) or `single` (1 source), colour-coded
- Last modified (max timestamp across contributing rows)

Query pulls from `_id_{target}` joined to `_resolved_{target}`:
```sql
SELECT
    r._entity_id,
    r.email,                     -- identity field(s)
    r.first_name, r.last_name,   -- display fields
    array_agg(DISTINCT i._mapping ORDER BY i._mapping) AS sources
FROM _resolved_person r
JOIN _id_person i USING (_entity_id)    -- _entity_id_resolved = _entity_id in resolved
GROUP BY r._entity_id, r.email, r.first_name, r.last_name
ORDER BY r._entity_id
LIMIT 50 OFFSET ?;
```

A `?merged=true` filter shows only entities that were actually merged across sources.

### `/ui/model/{target}/{entity_id}` — Entity Detail / Merge Inspector

The most diagnostic page. For a single resolved entity, shows exactly what happened during resolution:
which source contributed which value, and why the winner won.

**Section 1 — Golden Record**
The final resolved values from `_resolved_{target}` displayed as a clean key→value table.

**Section 2 — Field Resolution Matrix**
A table with one row per field, one column per contributing source mapping:

| Field | Winner | hubspot_contacts | tripletex_contacts | Why |
|---|---|---|---|---|
| email | bob@acme.com | bob@acme.com | bob@acme.com | identity (both agree) |
| first_name | Bob | Bob (priority 1) | Robert (priority 2) | coalesce · lowest priority wins |
| phone | +47 123 | — | +47 123 | last_modified · newest wins |
| last_name | Smith | Smith (priority 1) | — | coalesce · only source |

- **Winner** column = value from `_resolved_{target}`
- Per-source columns = `_fwd_{mapping}` value for this `_entity_id_resolved`
- **Why** column = strategy name + the deciding factor (priority number or timestamp comparison)

Query joins `_id_{target}` with `_resolved_{target}` for the given entity:
```sql
SELECT i._mapping, i._priority, i._last_modified,
       i.first_name, i._priority_first_name, i._ts_first_name,
       i.email, i.phone, ...
FROM _id_person i
WHERE i._entity_id_resolved = $1
ORDER BY i._mapping;
```
The dashboard reads `mapping.yaml` at startup to know which fields exist and what strategy each uses,
so it can construct the "Why" column explanation without hard-coding field names.

**Section 3 — Raw Source Records**
Expandable cards, one per contributing source row, showing the raw `data` JSONB from `inout_src_*`.
Linked via `_src_id` from `_id_{target}`.

### `/ui/sync-status/{target}` — Cross-System Sync Matrix

A matrix view of every resolved entity against every destination system, answering "where is this
entity right now in each connected system?" at a glance. This is most useful for diagnosing why a
change in HubSpot hasn't arrived in Tripletex yet, or why writeback is silently stuck.

#### Page layout

One row per resolved entity. Columns: entity key fields, then one column per `written_state` block
declared in `mapping.yaml` — for the current `mapping.yaml` that is:

| Entity key | hubspot_contacts | tripletex_contacts | (for person) |
| Entity key | hubspot_companies | tripletex_customers | (for company) |
| Entity key | hubspot_associations | — | (for association) |

Each cell is a status badge. Rows default sorted by "worst status first" so failures surface to the
top regardless of entity ID.

#### Status values and their sources

| Badge | Meaning | Where it comes from |
|---|---|---|
| **up-to-date** | OSI-Mapping determined no change needed | `inout_dst_{c}_{d}._action = 'noop'` |
| **pending insert** | New entity, not yet written | `_action = 'insert'` AND `_status = 'pending'` in dst table |
| **pending update** | Existing entity, change queued | `_action = 'update'` AND `_status = 'pending'` |
| **pending delete** | Deletion queued | `_action = 'delete'` AND `_status = 'pending'` |
| **retrying** | Last write failed, automatic retry in progress | Most recent `inout_ops_writeback_result` row has `status = 'failed'` AND dead-letter row absent |
| **failed** | Permanently dead-lettered, no further retries | Row exists in `inout_dl_writeback_{c}_{d}` |
| **not mapped** | Entity has no row in this destination | No row in dst table and no lwstate |

Badge colours: green (up-to-date), amber (pending *), orange (retrying), red (failed), grey (not mapped).

A second line inside each cell shows the last written timestamp from `inout_dst_{c}_{d}_lwstate._written_at`
when present (e.g. "written 2 min ago").

#### Status derivation query

Status per entity × destination is determined with a single LEFT JOIN chain per target:

```sql
-- per-entity status for one destination (e.g. hubspot_contacts)
SELECT
    r._entity_id,
    r.email,
    COALESCE(
        CASE WHEN dl.external_id IS NOT NULL THEN 'failed'
             WHEN wr.status = 'failed'       THEN 'retrying'
             WHEN dst._action = 'noop'       THEN 'up-to-date'
             WHEN dst._action IS NOT NULL     THEN 'pending ' || dst._action
        END,
        'not mapped'
    )                                        AS sync_status,
    lw._written_at                           AS last_written
FROM _resolved_person r
LEFT JOIN inout_dst_hubspot_contacts         dst ON dst.cluster_id = r._entity_id
LEFT JOIN inout_dst_hubspot_contacts_lwstate lw  ON lw.cluster_id  = r._entity_id
LEFT JOIN inout_dl_writeback_hubspot_contacts dl ON dl.external_id = dst.external_id
LEFT JOIN LATERAL (
    SELECT status FROM inout_ops_writeback_result
    WHERE connector = 'hubspot' AND datatype = 'contacts'
      AND external_id = dst.external_id
    ORDER BY processed_at DESC LIMIT 1
) wr ON TRUE
ORDER BY
    CASE WHEN dl.external_id IS NOT NULL THEN 0
         WHEN wr.status = 'failed'       THEN 1
         WHEN dst._action != 'noop'      THEN 2
         ELSE 3
    END;
```

The dashboard runs this per destination, then zips the results together in Python by `_entity_id`
before rendering the template. For the few hundreds of entities in the PoC this is fine; at scale
this becomes a candidate for a materialised view.

#### Filters and drill-through

- **Status filter** — show only entities with at least one non-`up-to-date` destination (the default
  for the PoC since most rows are healthy)
- **Destination filter** — focus on a single connector column
- Clicking a status badge on a row opens the **entity detail** page (`/ui/model/{target}/{entity_id}`)
  scrolled to the "Raw Source Records" section for that destination
- Clicking a `failed` badge opens a dead-letter detail panel inline showing `error_message`,
  `failed_at`, `requeue_count`, and a **Requeue** button (→ `POST /api/dead-letter/{c}/{d}/{id}/requeue`)
- **Requeue all failed for target** bulk action button at the top of the page

#### Requeue API

| Method | Path | Action |
|---|---|---|
| `POST` | `/api/dead-letter/{connector}/{datatype}/{dl_id}/requeue` | Sets `requeued_at = now()`, increments `requeue_count`, clears `_action = 'dead_lettered'` back to `'update'` in the dst table |
| `POST` | `/api/dead-letter/{connector}/{datatype}/requeue-all` | Requeues all non-exhausted DL rows for that destination |

Both proxy directly to the database via `db.py`; the writeback daemon picks up the requeued rows on
its next poll cycle without any other intervention.

### `/ui/trace` — Entity Tracer

A universal search bar that accepts any identifier and resolves it to a full pipeline trace — from
raw source record through every intermediate layer to the golden record and out to writeback
destinations. The entry point can be any of:

- A **source record ID** (`_src_id` / `record_id`) from a source table — e.g. a HubSpot contact ID
- A **golden entity ID** (`_entity_id`) from the model layer
- A **writeback destination record ID** from an `inout_dst_*_lwstate` table

The tracer auto-detects the entry type by searching all three layers in parallel.

#### Search UX

A single text input with an optional scope dropdown (All / Source / Entity / Writeback). On submit,
the dashboard queries the DB and renders a vertical pipeline trace — not a generic JSON dump, but a
structured view that follows the data as it moved through each stage.

#### Trace result layout — top-down

```
[SOURCE RECORD]
  connector: hubspot · datatype: contacts · record_id: 12345
  _ingested_at: 2026-03-30 14:22:01 · _deleted: false
  Raw data: { … collapsed JSONB … }
        ↓  mapped via  hubspot_contacts
[FORWARD ROW  (_fwd_hubspot_contacts)]
  _entity_id: a3f8…   _priority: 1   _last_modified: 2026-03-30
  email: bob@acme.com   first_name: Bob   last_name: Smith   phone: —
        ↓  merged into cluster
[GOLDEN ENTITY  (_resolved_person)]
  _entity_id: a3f8…   (merged with tripletex_contacts row 9876)
  email: bob@acme.com   first_name: Bob (hubspot wins, priority 1)   phone: +47 123 (tripletex, last_modified)
        ↓  reverse-projected via  hubspot_contacts writeback
[WRITEBACK DESTINATION  inout_dst_hubspot_contacts_lwstate]
  cluster_id: a3f8…   _written_at: 2026-03-30 14:25:17
  written: { … }
        ↓  also reverse-projected via  tripletex_contacts writeback
[WRITEBACK DESTINATION  inout_dst_tripletex_contacts_lwstate]
  cluster_id: a3f8…   _written_at: 2026-03-30 14:25:19
  written: { … }
```

Each stage is a collapsible card. Stages that are missing (e.g. no writeback state yet) are shown
greyed out with a "not yet written" note rather than omitted.

#### Three search entry points

**From the source** — user pastes a raw source record ID.

1. Search all `inout_src_*` tables (keyed by `record_id` or `external_id`) — exactly one hit expected.
2. Look up that `_src_id` in `_id_{target}` via `WHERE _src_id = $1 AND _mapping = $2` to get
   `_entity_id_resolved`.
3. Load the golden record from `_resolved_{target}`.
4. Find all other `_id_*` rows in the same cluster (the sibling sources that merged in).
5. Load all `inout_dst_*_lwstate` rows for this cluster.

SQL skeleton for step 2:
```sql
SELECT _entity_id_resolved, _mapping
FROM _id_person
WHERE _src_id = $1
-- repeat for _id_company, _id_person_company_association
```

**From the golden entity ID** — user pastes or clicks into a known entity ID.

1. Look up the entity in all `_id_*` views (query each in parallel, stop at first match).
2. Load the full cluster — all `_id_*` rows for that `_entity_id_resolved`.
3. For each row, join back to `inout_src_*` to get the raw source record.
4. Load the golden record and all `inout_dst_*_lwstate` rows.

**From a writeback destination** — user pastes a destination record ID (e.g. a Tripletex customer ID
that was written back).

1. Search all `inout_dst_*_lwstate` tables by their PK or by `written->>'id'` (JSONB contains).
2. Recover the `cluster_id` from the matched row.
3. Use that `cluster_id` as the entity ID and proceed as "from the golden entity ID" above.

SQL skeleton:
```sql
SELECT cluster_id FROM inout_dst_hubspot_contacts_lwstate WHERE cluster_id = $1
UNION ALL
SELECT cluster_id FROM inout_dst_tripletex_contacts_lwstate WHERE cluster_id = $1
-- etc.
```

#### Sibling sources panel

When an entity was merged, the trace also shows a **sibling sources** section: the other source
records that were pulled into the same cluster, with their own raw data collapsed. This makes it
immediately clear why two records were judged identical (shared email / org_number / domain) and
what values each contributed.

#### Cross-target tracing

An association entity (`person_company_association`) references a `person_id` and a `company_id`.
The tracer renders clickable links for those IDs so the user can jump directly to the person or
company trace without a new search.

### `/ui/dag` — OSI Mapping DAG

A Mermaid diagram of the entire `mapping.yaml`: which source tables feed which canonical targets, and
which targets feed writeback destinations (`inout_dst_*`). Rendered with the Mermaid CDN JS library
inside a `<pre class="mermaid">` block — no build step required.

Example output shape:
```
flowchart LR
  inout_src_hubspot_contacts --> _id_person
  inout_src_tripletex_contacts --> _id_person
  _id_person --> _resolved_person
  _resolved_person --> person
  person --> inout_dst_hubspot_contacts_lwstate
  person --> inout_dst_tripletex_contacts_lwstate
  ...
```

The diagram is generated server-side from `mapping.yaml` (no DB query needed) by the dashboard's
`db.py` helper that walks the mappings and emits Mermaid edge strings.

Can also show the schema-manager reconcile status: last reconcile time, whether it is in
`running` / `stopped` / `shadow` state (from the `component_state` table).

### `/ui/control` — Actions Panel

Buttons and status for manual operations:

| Action | HTTP call |
|---|---|
| Force full re-sync (connector + datatype) | `POST /api/pipelines/{c}/{d}/force-sync` → proxies to ingest engine |
| Pause connector | `POST /api/pipelines/{c}/{d}/pause` |
| Resume connector | `POST /api/pipelines/{c}/{d}/resume` |
| Trigger schema reconcile | `POST /api/schema/reconcile` → proxies to schema-manager |
| Trigger schema reconcile (dry-run) | `POST /api/schema/reconcile?dry_run=true` |

All actions respond inline (no full-page reload) using `fetch()` + a status badge update. SSE pushes
the pipeline status update within seconds.

## Live Updates (SSE)

`GET /events` — emit a JSON event whenever:
- A new row appears in `inout_ops_sync_run` (poll every 5 s)
- `component_state.desired` changes (poll every 5 s)

The dashboard page listens on this stream and updates status badges without a full reload.
The pattern is identical to the simulator's `events.py` / `sse.py`.

## Internal API (not user-facing)

These endpoints are consumed by the UI's own JavaScript:

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/schema` | View checklist + component gate + pgtrickle stream state + reconcile log |
| `GET` | `/api/pipelines` | Per-datatype flow counts + last sync run |
| `GET` | `/api/pipelines/{c}/{d}` | Detail: runs + DAG stages |
| `POST` | `/api/pipelines/{c}/{d}/force-sync` | Proxy → ingest engine |
| `POST` | `/api/pipelines/{c}/{d}/pause` | Proxy → ingest engine |
| `POST` | `/api/pipelines/{c}/{d}/resume` | Proxy → ingest engine |
| `GET` | `/api/model` | Cluster counts per target from `_id_*` |
| `GET` | `/api/model/{target}` | Paginated entity list from `_id_*` + `_resolved_*` |
| `GET` | `/api/model/{target}/{entity_id}` | Full merge detail: field matrix + raw source rows |
| `GET` | `/api/sync-status/{target}` | Full sync matrix: entity × destination status |
| `POST` | `/api/dead-letter/{c}/{d}/{dl_id}/requeue` | Requeue a single dead-letter row |
| `POST` | `/api/dead-letter/{c}/{d}/requeue-all` | Requeue all non-exhausted DL rows |
| `GET` | `/api/trace?q={id}` | Auto-detect ID type; return full pipeline trace JSON |
| `GET` | `/api/trace/source/{connector}/{datatype}/{record_id}` | Trace from a specific source record |
| `GET` | `/api/trace/entity/{entity_id}` | Trace from a golden entity ID |
| `GET` | `/api/trace/destination/{connector}/{datatype}/{cluster_id}` | Trace from a writeback destination |
| `POST` | `/api/schema/reconcile` | Proxy → schema-manager |
| `GET` | `/health` | Always `{"status":"ok"}` |

## File Layout

```
dashboard/
  pyproject.toml          # [project.scripts] sesam-dashboard = "sesam_dashboard.cli:app"
  src/sesam_dashboard/
    cli.py                # Typer CLI: sesam-dashboard serve --config ...
    app.py                # create_app() — FastAPI factory, mounts /ui and /api
    config.py             # Pydantic settings (database DSN, ingest URL, schema-manager URL)
    db.py                 # asyncpg pool; helpers: fetch_view_checklist, fetch_component_gate,
                          #   fetch_pgtrickle_stream_state, fetch_source_table_state,
                          #   fetch_pipeline_flow_counts,
                          #   fetch_sync_runs, count_table, fetch_dag_views,
                          #   fetch_model_summary, fetch_entity_list, fetch_entity_detail,
                          #   fetch_sync_matrix, requeue_dead_letter, requeue_all_dead_letter,
                          #   trace_from_source, trace_from_entity, trace_from_destination,
                          #   search_all_layers (fan-out search for auto-detect)
    mapping_reader.py     # Parses mapping.yaml into a typed structure used by db.py and dag generation
    engine_client.py      # httpx AsyncClient wrapper for ingest engine API
    schema_client.py      # httpx AsyncClient wrapper for schema-manager API
    sse.py                # SSE stream (polls DB + engine; yields JSON events)
    ui/
      router.py           # GET /ui (schema+pipeline landing), /ui/schema, /ui/pipelines,
                          #   /ui/pipelines/{c}/{d}, /ui/model, /ui/model/{t}, /ui/model/{t}/{id},
                          #   /ui/sync-status/{t}, /ui/trace, /ui/dag, /ui/control
      api.py              # GET/POST /api/...
      templates/
        base.html         # Shared nav + SSE <script>
        schema.html       # Landing: view checklist, component gate, pgtrickle stream state,
                          #   source table checklist, reconcile log, migration history
        overview.html     # Pipeline data-flow counts table
        pipeline.html     # Sync-run timeline + DAG stages
        model.html        # Golden record explorer: target cards + cluster stats
        entity_list.html  # Paginated entity table with source badges
        entity_detail.html # Field resolution matrix + raw source records
        sync_matrix.html  # Cross-system status matrix with inline dead-letter panel
        trace.html        # Search bar + collapsible pipeline trace cards
        dag.html          # Mermaid DAG (server-generated Mermaid text)
        control.html      # Actions panel
      static/
        dashboard.css     # Minimal styling (copy simulator approach)
        dashboard.js      # SSE listener + fetch actions
```

## K8s Deployment

New file `k8s/base/dashboard.yaml`:

```yaml
# Deployment + Service
# image: sesam-dashboard:latest
# command: sesam-dashboard serve --config /config/dashboard.yaml
# port: 8888
# envFrom: sesam-credentials (for DASHBOARD_DATABASE_URL)
# init-container: wait for schema-manager /ready (same pattern as ingest.yaml)
```

`dashboard.yaml` added to `k8s/base/kustomization.yaml` resources list.

Skaffold port-forward added in `skaffold.yaml`:
```yaml
- resourceType: Service
  resourceName: sesam-dashboard
  namespace: sesam-poc
  port: 8888
  localPort: 8888
```

New `docker/dashboard.Dockerfile` — mirrors `osi-mapping.Dockerfile` pattern: thin Python image,
`COPY dashboard/ /app`, `pip install /app`, `CMD ["sesam-dashboard", "serve"]`.

## Configuration (`dashboard.yaml` in ConfigMap)

```yaml
database:
  dsn: "${DASHBOARD_DATABASE_URL}"
ingest_url: http://inandout-ingest:9090
schema_manager_url: http://inandout-schema-manager:9080
server:
  listen: "0.0.0.0:8888"
sse_poll_interval: 5   # seconds
```

## Open Questions

- Add authentication (basic-auth or OAuth2 proxy) before exposing outside the cluster? For PoC: no.
- Should the `/ui/model/{target}/{entity_id}` field matrix include a confidence score alongside the
  priority/timestamp "Why" column? The engine doesn't produce one natively, but a simple heuristic
  (e.g. "both sources agree → green, only one source → yellow, sources conflict → orange") is easy
  to add in the dashboard layer without touching the engine.
- The `requeue` action writes directly to the database from the dashboard. This is intentional
  for the PoC (no separate admin API in the writeback engine). Flag as a future clean-up item if the
  writeback engine exposes its own dead-letter management API.
- At scale, the sync matrix query (`LATERAL` per entity per destination) should be replaced by a
  materialised view or a pg-trickle IVM over `inout_dst_*` + `inout_dl_writeback_*`. Not needed for PoC.
- Should `/ui/trace` also be reachable by clicking a row anywhere else in the dashboard (overview,
  entity list, writeback table)? Yes — every record ID in the UI should be a link to its trace.
  Implement after the tracer page itself works, by adding `href="/ui/trace?q={id}"` to row links.
- The "from destination" search does a JSONB `@>` scan across all `inout_dst_*_lwstate` tables which
  could be slow without an index. For PoC the row counts are tiny; note in a follow-up that a
  GIN index on the `written` column would be needed at scale.
- Should deleted entities (`_deleted = true` in `inout_src_*`) still appear in the entity list, marked
  as deleted? Start with them hidden; add a toggle later.
- Should deleted pipeline records (from `inout_src_*` where `_deleted = true`) be shown separately or
  subtracted from the count? Show both: `total / deleted` in one column.
- Should `/ui/control` actions also appear inline on the overview table (contextual buttons per row)?
  Probably yes, but start with a separate control page to keep the overview uncluttered.

## Related Plans

- [observability-setup.md](observability-setup.md) — Prometheus + Grafana for metrics; the dashboard
  complements rather than replaces that (dashboard = human-friendly operational view; Grafana = time-series)
- [data-ingestion.md](../ingest/data-ingestion.md) — describes `inout_ops_sync_run`, watermarks, control table
- [schema-manager.md](../coordination/schema-manager.md) — describes the reconcile lifecycle
