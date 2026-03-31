# Schema Manager — Implementation Plan

The schema-manager is an always-running service that owns the full lifecycle of the database schema and acts as supervisor for the ingest and writeback components. No other component writes DDL or manages its own schema.

Related: [ADR-002](../adrs/002-postgresql-storage.md), [ADR-004](../adrs/004-osi-mapping-spec.md)

## Motivation

Schema ownership is currently scattered: dlt auto-creates staging tables, Alembic manages operational tables inside the in-and-out engine, osi-mapping generates view SQL, and a K8s Job glues them together with shell scripts. This creates implicit ordering dependencies, a chicken-and-egg problem on first deploy, and a dangerous window during view recreation where writeback can see incomplete data and issue spurious deletes.

Centralising schema lifecycle in a single service eliminates all of these problems by construction: components only run when the database is in a known-good state because the schema-manager controls when they start and stop.

## Architecture

```
                      schema-manager (always running)
                     ┌────────────────────────────────┐
                     │                                │
 mapping.yaml ──────►│  1. Compute desired schema     │
 connectors/*.yaml ─►│  2. Apply DDL (single owner)   │
                     │  3. Manage component lifecycle  │
                     │  4. Watch for config changes    │
                     │                                │
                     └────┬──────────────┬────────────┘
                          │              │
                     controls        controls
                          │              │
                     ┌────▼────┐    ┌────▼───────┐
                     │ ingest  │    │ writeback   │
                     └─────────┘    └─────────────┘

 osi-mapping CLI: called by schema-manager as a library/subprocess.
                  Not a runtime service.
```

## Component State Table

The schema-manager communicates with ingest and writeback through a `component_state` table in the database. This is the database-driven readiness approach (Option C from the design discussion).

```sql
CREATE TABLE IF NOT EXISTS component_state (
    component   TEXT PRIMARY KEY,           -- 'ingest', 'writeback'
    desired     TEXT NOT NULL DEFAULT 'stopped',  -- 'running' | 'stopped'
    schema_version TEXT,                    -- hash of config that produced current schema
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Ingest and writeback poll this table on a short interval (e.g. 5 s). When `desired = 'stopped'`, the component pauses its main loop (stops polling APIs / stops processing sync queue). When `desired = 'running'`, it resumes. This is **not** a process kill — the component stays alive and healthy but idles.

This approach works identically inside Kubernetes, in Docker Compose, or running processes locally — no platform API calls required.

## Schema-Manager Lifecycle

```
startup
│
├─► Connect to Postgres, wait for readiness
├─► Ensure component_state table exists
├─► Set desired = 'stopped' for all components
│
├─► Read all config inputs:
│   ├── connectors/*.yaml   (source entities, fields)
│   ├── mapping.yaml        (targets, views, strategies)
│   └── in-and-out Alembic revision history
│
├─► Compute desired database state:
│   ├── Staging table stubs from mapping.yaml sources: section
│   ├── Operational tables via Alembic revisions
│   ├── Generated views via osi-engine render
│   └── Stream tables via convert_matviews_to_pgtrickle.py
│
├─► Compute config hash (SHA-256 of mapping.yaml + connectors/*.yaml)
├─► Compare to schema_version in component_state
│
├─► If hash unchanged AND all tables/views exist:
│   └── Skip DDL, go straight to start components
│
├─► If hash changed OR schema missing:
│   ├── Stop components: SET desired = 'stopped'
│   ├── Wait for components to acknowledge (health endpoint reports idle)
│   ├── Apply DDL in dependency order:
│   │   1. Create/update staging table stubs (from sources: in mapping.yaml)
│   │   2. Run Alembic upgrade (operational tables)
│   │   3. Drop existing generated views and stream tables
│   │   4. Call osi-engine render → SQL
│   │   5. Convert to pg-trickle stream SQL
│   │   6. Apply generated SQL in a transaction
│   ├── Wait for stream tables to be populated (row count > 0 or watermark)
│   └── Update schema_version in component_state
│
├─► Start components:
│   ├── SET desired = 'running' for ingest
│   ├── Wait for ingest health check to report ready
│   ├── SET desired = 'running' for writeback
│   └── (writeback starts after ingest, so initial data is flowing)
│
└─► Enter watch loop:
    ├── Periodic config hash check (detect mapping.yaml / connector changes)
    │   └── If changed → re-enter schema diff (stops components first)
    ├── Component health monitoring
    │   └── If a component crashes → log, optionally restart
    ├── schema-manager own health endpoint (/health, /ready on port 9080)
    └── Sleep interval (e.g. 30 s between checks)
```

## DDL Dependency Order

The schema-manager applies DDL in a strict order that respects cross-component references:

```
Step 1: Staging table stubs
        ├── Read sources: section from mapping.yaml
        ├── For each source, CREATE TABLE IF NOT EXISTS with columns:
        │   external_id TEXT PRIMARY KEY, data JSONB, _loaded_at TIMESTAMPTZ
        └── These are stubs; ingest will INSERT/UPSERT into them

Step 2: Operational tables (Alembic)
        ├── entity_link
        ├── entity_cluster_member
        ├── cross_system_link
        ├── sync_state
        └── sync_task

Step 3: Generated views + stream tables
        ├── DROP all objects in the generated layer
        ├── Run osi-engine render mapping.yaml → matviews.sql
        ├── Run convert_matviews_to_pgtrickle.py → pgtrickle.sql
        └── Apply pgtrickle.sql (creates stream tables + views)

Step 3 depends on Steps 1 and 2 because the generated views join
staging tables and entity_cluster_member.
```

## Staging Table Stub Creation

Currently dlt auto-creates staging tables when ingest first runs, which creates a chicken-and-egg problem: generated views reference tables that don't exist yet on first deploy.

The schema-manager solves this by creating **stub tables** before views are generated. The stub schema is derived from the `sources:` section of `mapping.yaml`:

```yaml
# mapping.yaml
sources:
  hubspot_contacts:
    primary_key: external_id
    table: inout_src_hubspot_contacts
```

The schema-manager creates:

```sql
CREATE TABLE IF NOT EXISTS inout_src_hubspot_contacts (
    external_id  TEXT PRIMARY KEY,
    data         JSONB NOT NULL DEFAULT '{}',
    _loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The stub uses a minimal schema (external_id + JSONB payload) that is compatible with how dlt writes data. If dlt adds columns later (schema evolution), that's fine — the views only read from the `data` JSONB column via `source_path` expressions, so added columns don't break anything.

If a source is removed from `mapping.yaml`, the schema-manager drops the orphaned stub table (after confirming no other mappings reference it).

## Changes to In-and-Out Engine (Ingest)

### Remove dlt schema creation responsibility

Currently the ingest component uses dlt which auto-creates and evolves staging tables. With the schema-manager, tables always pre-exist. The ingest component must be configured to **not** create or alter tables, only write rows.

Required changes:

1. **Disable dlt schema evolution.** Configure dlt to skip `CREATE TABLE` and `ALTER TABLE` statements. dlt supports this via `schema_contract` settings:
   ```python
   # In the ingest pipeline setup
   pipeline.run(
       data,
       schema_contract={
           "tables": "evolve",       # allow new tables  → change to "discard_row"
           "columns": "evolve",      # allow new columns → change to "discard_value"
       }
   )
   ```
   Alternatively, use `"freeze"` to hard-fail if dlt encounters an unknown table — this makes misconfigurations visible immediately.

2. **Remove Alembic dependency.** The ingest image no longer needs to bundle Alembic or run `inandout db upgrade`. This command moves to the schema-manager.

3. **Add component_state polling.** The ingest main loop must check `component_state` and pause when `desired = 'stopped'`:
   ```python
   while True:
       state = db.execute(
           "SELECT desired FROM component_state WHERE component = 'ingest'"
       ).scalar()
       if state != 'running':
           log.info("Paused by schema-manager, waiting...")
           time.sleep(5)
           continue
       # ... normal ingest cycle ...
   ```

4. **Remove the wait-for-migrations init container.** The schema-manager handles all ordering. The ingest deployment no longer needs the init container that polls for `alembic_version`.

5. **Health endpoint must report pause state.** When paused, `/ready` should return `503` (not ready to receive traffic) but `/health` should return `200` (process is alive). This allows the schema-manager to distinguish "paused and healthy" from "crashed".

## Changes to In-and-Out Engine (Writeback)

Same pattern as ingest:

1. **Add component_state polling.** The writeback sync loop checks `desired` before processing the sync queue:
   ```python
   while True:
       state = db.execute(
           "SELECT desired FROM component_state WHERE component = 'writeback'"
       ).scalar()
       if state != 'running':
           log.info("Paused by schema-manager, waiting...")
           time.sleep(5)
           continue
       # ... normal writeback cycle (read sync_queue, diff, CAS write) ...
   ```

2. **Remove the wait-for-migrations init container.** Same as ingest.

3. **Health endpoint pause-awareness.** Same as ingest — `/ready` returns 503 when paused.

4. **No schema management code.** Writeback already doesn't manage DDL, but it currently assumes views exist on startup. With the component_state gate, this assumption is guaranteed: if writeback is told to run, the views are ready.

## Changes to OSI-Mapping

No changes. The osi-mapping engine is already a stateless CLI: `osi-engine render mapping.yaml → SQL`. The schema-manager calls it as a subprocess. The Docker image (`osi-mapping-engine`) is reused as-is.

The only change is **who calls it**: the schema-manager init container or subprocess, instead of the migrate-job init container.

## Changes to Alembic

Alembic migrations are extracted from the in-and-out engine and become the schema-manager's responsibility. Two options:

**Option A (recommended for PoC):** The schema-manager image includes the in-and-out engine binary and calls `inandout db upgrade` as a subprocess. This requires no changes to how Alembic revisions are authored — they stay in vendor/in-and-out.

**Option B (cleaner long-term):** The Alembic revision files are extracted into a shared location (e.g. `migrations/` at the repo root) and the schema-manager runs Alembic directly. This removes the in-and-out engine dependency from the schema-manager image.

For now, go with Option A. The schema-manager image is built FROM the in-and-out engine image (multi-stage) or bundles the binary.

## Component State Protocol

### States

| `desired` value | Component behaviour |
|----------------|---------------------|
| `stopped` | Component pauses main loop. Health: `/health` → 200, `/ready` → 503 |
| `running` | Component runs normally. Health: `/health` → 200, `/ready` → 200 |

### Polling behaviour

Components poll `component_state` every 5 seconds. This is simple, reliable, and has negligible database load.

### Startup behaviour

When a component starts up and finds no row in `component_state` for itself, it inserts one with `desired = 'stopped'` and waits. The schema-manager will set it to `running` when ready.

### Graceful pause

When the schema-manager sets `desired = 'stopped'`, it then waits for the component to finish its current work cycle. It can verify this by:
- Polling the component's `/ready` endpoint until it returns 503
- Or checking a `current_state` column that the component updates (e.g. `'idle'`, `'processing'`)

For the PoC, polling `/ready` is sufficient.

## Migration State Tracking

The schema-manager tracks what it has applied:

```sql
CREATE TABLE IF NOT EXISTS migration_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Example rows:
-- ('config_hash',     'sha256:abc123...')         -- hash of all input configs
-- ('last_applied_at', '2026-03-31T12:00:00Z')     -- when DDL was last applied
-- ('osi_mapping_sql', 'sha256:def456...')          -- hash of generated SQL
-- ('alembic_head',    'abc123def456')              -- last alembic revision applied
```

When the config hash matches, the schema-manager skips DDL entirely — deployments with no config changes are instant.

## Kubernetes Manifest Changes

### New: schema-manager.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sesam-schema-manager
  namespace: sesam-poc
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sesam-schema-manager
  template:
    spec:
      containers:
        - name: schema-manager
          image: sesam-schema-manager:latest
          command: ["schema-manager", "run"]
          ports:
            - name: health
              containerPort: 9080
          envFrom:
            - secretRef:
                name: sesam-credentials
          volumeMounts:
            - name: mapping
              mountPath: /mapping
            - name: connectors
              mountPath: /connectors
            - name: config
              mountPath: /config
      volumes:
        - name: mapping
          configMap:
            name: osi-mapping-config
        - name: connectors
          configMap:
            name: inandout-connectors
        - name: config
          configMap:
            name: inandout-config
```

### Delete: migrate-job.yaml

The entire Job and its pre-install hook are removed. The schema-manager replaces it.

### Modify: ingest.yaml

- Remove the `wait-for-migrations` init container
- No other manifest changes (component_state polling is a code change, not a manifest change)

### Modify: writeback.yaml

- Remove the `wait-for-migrations` init container

### Modify: kustomization.yaml

```diff
 resources:
   - namespace.yaml
   - secret.yaml
   - configmap.yaml
   - postgres.yaml
-  - migrate-job.yaml
+  - schema-manager.yaml
   - ingest.yaml
   - writeback.yaml
   - simulator.yaml
```

### Modify: skaffold.yaml

- Remove the `kubectl delete job sesam-migrate` pre-deploy hook (no longer needed)
- Add the schema-manager image build:
  ```yaml
  - image: sesam-schema-manager
    context: .
    docker:
      dockerfile: docker/schema-manager.Dockerfile
  ```

### Modify: justfile

- Replace `just migrate` recipe: instead of deleting/recreating a K8s Job, it can either:
  - Trigger the schema-manager to re-check configs (e.g. `curl http://localhost:9080/reconcile`)
  - Or for local dev: run the schema-manager CLI directly against localhost Postgres

## Schema-Manager Docker Image

The schema-manager image needs:
- Python runtime (base: `python:3.13-slim`)
- `osi-engine` binary (COPY from osi-mapping-engine build stage)
- `convert_matviews_to_pgtrickle.py` script (COPY from vendor/pg-trickle)
- `inandout` binary or Alembic CLI (COPY from inandout-engine build stage)
- `psql` client (for applying generated SQL)
- The schema-manager Python code itself

Multi-stage Dockerfile:

```dockerfile
FROM osi-mapping-engine:latest AS osi
FROM inandout-engine:latest AS inandout

FROM python:3.13-slim
# osi-engine CLI
COPY --from=osi /usr/local/bin/osi-engine /usr/local/bin/
COPY --from=osi /usr/local/bin/convert_matviews_to_pgtrickle.py /usr/local/bin/
# inandout CLI (for Alembic migrations)
COPY --from=inandout /app/inandout /usr/local/bin/
# psql client
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*
# Schema-manager code
COPY schema-manager/ /app/
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt
ENTRYPOINT ["python", "-m", "schema_manager"]
```

## Schema-Manager Python Package Layout

```
schema-manager/
├── pyproject.toml
├── requirements.txt
├── schema_manager/
│   ├── __init__.py
│   ├── __main__.py          # CLI entrypoint
│   ├── reconciler.py        # Core loop: compute desired state, diff, apply
│   ├── config.py            # Read and hash mapping.yaml + connectors
│   ├── stubs.py             # Generate staging table stub DDL from sources:
│   ├── osi.py               # Call osi-engine render + convert script
│   ├── alembic_runner.py    # Call inandout db upgrade as subprocess
│   ├── applier.py           # Apply generated SQL to Postgres
│   ├── component_gate.py    # Manage component_state table
│   ├── health.py            # /health and /ready endpoints
│   └── watcher.py           # Watch loop: periodic config hash check
```

## Startup Ordering (After Implementation)

```
1. Postgres starts, becomes ready
2. Schema-manager starts, connects to Postgres
3. Schema-manager creates component_state, migration_state tables
4. Schema-manager sets desired = 'stopped' for ingest and writeback
5. Ingest and writeback start, find desired = 'stopped', idle
6. Schema-manager applies DDL (stubs → Alembic → views → streams)
7. Schema-manager waits for stream tables to be ready
8. Schema-manager sets desired = 'running' for ingest
9. Ingest begins polling APIs and writing to staging tables
10. Schema-manager sets desired = 'running' for writeback
11. Writeback begins processing sync queue
```

Steps 5-6 can happen concurrently — ingest and writeback are idling while the schema-manager works. No init containers, no Job ordering, no race conditions.

## Config Change Flow (Steady State)

```
1. Developer changes mapping.yaml (e.g. adds a field)
2. skaffold dev detects change, rebuilds ConfigMap, rolls out
3. Schema-manager's watch loop detects config hash change
4. Schema-manager sets desired = 'stopped' for writeback
5. Schema-manager waits for writeback to report idle
6. Schema-manager sets desired = 'stopped' for ingest (if staging stubs changed)
7. Schema-manager drops and recreates generated views + streams
8. Schema-manager waits for stream tables to rebuild
9. Schema-manager sets desired = 'running' for ingest, then writeback
```

If only mapping.yaml changed (no new sources), ingest can keep running during steps 6-8 — it writes to staging tables that are unaffected by view recreation. Only writeback must be stopped to prevent it from reading partially-rebuilt views.

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| Postgres unreachable | Schema-manager retries with exponential backoff. Components stay stopped. |
| osi-engine render fails | Schema-manager logs error, does not apply partial DDL, components stay stopped. Retry on next watch cycle. |
| Alembic upgrade fails | Same — log, skip, retry. |
| Generated SQL apply fails | Transaction rolls back. Old views remain (if this is a re-deploy) or no views exist (first deploy). Components stay stopped. |
| Schema-manager crashes | Components remain in their last `desired` state. If they were running, they continue running (safe — the schema hasn't changed). On schema-manager restart, it re-enters the reconcile loop. |
| Component crashes | Schema-manager observes via health check. Can log/alert. Kubernetes restartPolicy handles the actual restart. |

## What Gets Deleted

| Current artifact | Replaced by |
|-----------------|-------------|
| `k8s/base/migrate-job.yaml` | `k8s/base/schema-manager.yaml` |
| `wait-for-migrations` init container in ingest.yaml | `component_state` polling in ingest code |
| `wait-for-migrations` init container in writeback.yaml | `component_state` polling in writeback code |
| dlt auto-schema-creation in ingest | Staging table stubs created by schema-manager |
| `kubectl delete job` hook in skaffold.yaml | Not needed — schema-manager is a Deployment, not a Job |
| `just migrate` recipe (Job-based) | `just migrate` recipe (calls schema-manager API or runs CLI) |

## Implementation Sequence

1. **Create the `schema-manager/` Python package** with the reconciler, config reader, stub generator, osi-engine caller, SQL applier, and component gate.
2. **Create the Dockerfile** (`docker/schema-manager.Dockerfile`) as described above.
3. **Create `k8s/base/schema-manager.yaml`** Deployment manifest.
4. **Add component_state polling** to the in-and-out engine (ingest and writeback commands). This is a code change in `vendor/in-and-out`.
5. **Disable dlt schema creation** in the ingest pipeline config. Change in `vendor/in-and-out`.
6. **Remove `wait-for-migrations` init containers** from `ingest.yaml` and `writeback.yaml`.
7. **Delete `migrate-job.yaml`** and update `kustomization.yaml`.
8. **Update `skaffold.yaml`**: add schema-manager image build, remove Job delete hook.
9. **Update `justfile`**: replace Job-based migrate recipe.
10. **Test**: deploy to kind cluster, verify startup ordering and config-change flow.

## Open Questions

- Should the schema-manager expose a `/reconcile` HTTP endpoint for on-demand triggers, or rely purely on the periodic watch loop?
- How should we detect that pg-trickle stream tables are "fully populated" after recreation? Row count comparison, a watermark table, or a fixed delay?
- Should we add a `component_state.current_state` column for components to report back (`idle`, `processing`, `error`), or is polling `/ready` sufficient?
