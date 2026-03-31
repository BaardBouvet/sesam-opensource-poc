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
    desired     TEXT NOT NULL DEFAULT 'stopped',  -- 'running' | 'stopped' | 'shadow'
    schema_version TEXT,                    -- hash of config that produced current schema
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Ingest and writeback poll this table on a short interval (e.g. 5 s). When `desired = 'stopped'`, the component pauses its main loop (stops polling APIs / stops processing sync queue). When `desired = 'running'`, it resumes. When `desired = 'shadow'` (writeback only), it computes diffs but does not push to APIs. This is **not** a process kill — the component stays alive and healthy but idles or runs in dry-run mode.

This approach works identically inside Kubernetes, in Docker Compose, or running processes locally — no platform API calls required.

## Multi-Replica Coordination: Advisory Lock Barrier

Ingest and writeback can each run multiple replicas. The `component_state` table tells replicas **what to do**, but the schema-manager also needs to know **when every replica has actually stopped** before applying DDL. Polling `/ready` on a Kubernetes Service is useless here — it load-balances to a random replica.

The solution uses PostgreSQL advisory locks as a barrier:

- Each replica holds a **shared advisory lock** while doing work
- The schema-manager acquires an **exclusive advisory lock** before applying DDL
- Postgres guarantees the exclusive lock blocks until all shared locks are released

```
                     Advisory lock key: 0x5E5A0001

 Replica work cycle:              Schema-manager migration:

 pg_advisory_lock_shared(key)     1. SET desired = 'stopped'
 ... do one work cycle ...        2. pg_advisory_lock(key)  ← BLOCKS
 pg_advisory_unlock_shared(key)      until all shared locks released
 check desired → stopped? skip    3. ... apply DDL ...
                                  4. pg_advisory_unlock(key)
                                  5. SET desired = 'running'
```

This works because:
- Multiple replicas can hold a shared lock simultaneously (they don't block each other)
- The exclusive lock blocks until **all** shared locks are released
- If a replica crashes, its database session closes and the shared lock is auto-released
- No registration table, no heartbeats, no replica counting

### Replica work loop pattern

Every replica (ingest or writeback) follows this pattern:

```python
INGEST_LOCK_KEY    = 0x5E5A_0001  # ingest barrier
WRITEBACK_LOCK_KEY = 0x5E5A_0002  # writeback barrier

while True:
    state = db.execute(
        "SELECT desired FROM component_state WHERE component = %s", [self.name]
    ).scalar()

    if state != 'running':
        time.sleep(5)
        continue

    lock_key = INGEST_LOCK_KEY if self.name == 'ingest' else WRITEBACK_LOCK_KEY

    # Acquire shared lock — multiple replicas hold this simultaneously
    db.execute("SELECT pg_advisory_lock_shared(%s)", [lock_key])
    try:
        # Re-check after acquiring lock (schema-manager may have set stopped
        # between our check and lock acquisition — prevents TOCTOU race)
        state = db.execute(
            "SELECT desired FROM component_state WHERE component = %s", [self.name]
        ).scalar()
        if state != 'running':
            continue  # unlock in finally, then retry

        # ... do one work cycle ...
    finally:
        db.execute("SELECT pg_advisory_unlock_shared(%s)", [lock_key])
```

Key details:
- The shared lock is held only for **one work cycle**, not permanently
- The double-check after lock acquisition prevents a TOCTOU race
- The `finally` block guarantees unlock even if the cycle crashes

### Schema-manager migration sequence

```python
INGEST_LOCK_KEY    = 0x5E5A_0001
WRITEBACK_LOCK_KEY = 0x5E5A_0002

def apply_migration(db, config, tier):
    # 1. Signal affected replicas to stop
    if tier >= 2:
        db.execute("UPDATE component_state SET desired = 'stopped'")
    else:
        db.execute("UPDATE component_state SET desired = 'stopped' WHERE component = 'writeback'")

    # 2. Acquire exclusive locks for affected components.
    #    Blocks until all shared locks on each key are released.
    if tier >= 2:
        db.execute("SELECT pg_advisory_lock(%s)", [INGEST_LOCK_KEY])
    db.execute("SELECT pg_advisory_lock(%s)", [WRITEBACK_LOCK_KEY])

    try:
        # 3. No affected replica is doing work.
        apply_ddl(db, config)
    finally:
        # 4. Release exclusive locks
        db.execute("SELECT pg_advisory_unlock(%s)", [WRITEBACK_LOCK_KEY])
        if tier >= 2:
            db.execute("SELECT pg_advisory_unlock(%s)", [INGEST_LOCK_KEY])

    # 5. Wait for streams to rebuild, then resume
    wait_for_streams_ready(db)
    db.execute(
        "UPDATE component_state SET desired = 'running' "
        "WHERE component IN ('ingest', 'writeback')"
    )
```

### Edge cases

| Scenario | What happens |
|----------|-------------|
| Replica mid-batch when `desired = 'stopped'` | Finishes current cycle, releases shared lock, sees stopped, idles. Schema-manager's exclusive lock succeeds. |
| Replica crashes mid-batch | Database session closes → shared lock auto-released. Schema-manager proceeds. |
| New replica starts during migration | Sees `desired = 'stopped'`, never acquires shared lock, idles. |
| New replica races to acquire lock during migration | `pg_advisory_lock_shared()` blocks because schema-manager holds exclusive. Replica waits, then re-checks desired state. |
| Schema-manager crashes mid-DDL | Transaction rolls back. Exclusive lock auto-released. Replicas see `desired = 'stopped'`, stay idle. Schema-manager restarts, retries. |
| Schema-manager crashes after DDL, before setting `running` | Replicas stay stopped. Schema-manager restarts, detects hash matches, skips DDL, sets `running`. |

## Schema-Manager Leader Election

If Kubernetes restarts the schema-manager pod (OOM, node drain, rolling update), there is a brief window where two instances overlap. Both would try to set `desired = 'stopped'` and acquire the exclusive migration lock. While the lock prevents concurrent DDL, the loser could set `desired = 'stopped'` after the winner already set `running` — bouncing all replicas.

The schema-manager must acquire a **leader advisory lock** on startup and hold it for its entire lifetime:

```python
LEADER_LOCK_KEY = 0x5E5A_0000  # distinct from the migration barrier key

def try_become_leader(db) -> bool:
    """Non-blocking attempt to become leader. Returns True if acquired."""
    return db.execute(
        "SELECT pg_try_advisory_lock(%s)", [LEADER_LOCK_KEY]
    ).scalar()
```

Behaviour:
- On startup, the schema-manager calls `pg_try_advisory_lock()` (non-blocking)
- If it succeeds, this instance is the leader and runs the reconcile loop
- If it fails, another instance already holds the lock — this instance logs a warning and exits (Kubernetes restart policy will retry later, by which time the old instance is gone)
- When the leader's database session closes (crash, shutdown), the lock is auto-released and the next instance can claim it

This uses the same advisory lock mechanism as the migration barrier, so no additional infrastructure is needed. The leader lock key (`0x5E5A0000`) is distinct from the migration barrier key (`0x5E5A0001`).

## Database Connection Lifecycle for Advisory Locks

Advisory locks are **session-scoped** — they are tied to the database connection that acquired them. If a connection pool rotates or recycles connections, the lock silently disappears. This applies to both the schema-manager and all component replicas.

Rules:
- The schema-manager **must** use a **dedicated, long-lived connection** for the leader lock. This connection is held for the lifetime of the process. A separate connection (or pool) can be used for other queries.
- The schema-manager's migration sequence must use the **same connection** for `pg_advisory_lock()` and `pg_advisory_unlock()`. If using a pool, pin the connection for the duration of the migration.
- Component replicas must use the **same connection** for `pg_advisory_lock_shared()` and `pg_advisory_unlock_shared()` within a single work cycle. Since the lock is acquired and released within one cycle, this is straightforward: use a single connection (not from a pool) or pin a pooled connection for the cycle.

In practice, this means using SQLAlchemy's `engine.connect()` (dedicated connection) rather than `Session` (which may return connections to the pool between statements).

## Schema-Manager Lifecycle

```
startup
│
├─► Connect to Postgres, wait for readiness
├─► Acquire leader lock (exit if another instance holds it)
├─► Run self-upgrade (ensure component_state, migration_state, shadow_log exist)
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
├─► Compute config hash (SHA-256 of mapping.yaml + schema-relevant connector fields)
├─► Compare to schema_version in component_state
│
├─► If hash unchanged AND all tables/views exist:
│   └── Skip DDL, go straight to start components
│
├─► If hash changed OR schema missing:
│   ├── Stop components: SET desired = 'stopped'
│   ├── Acquire exclusive advisory lock (blocks until all replicas idle)
│   ├── Apply DDL in dependency order:
│   │   1. Create/update staging table stubs (from sources: in mapping.yaml)
│   │   2. Run Alembic upgrade (operational tables)
│   │   3. Drop existing generated views and stream tables
│   │   4. Call osi-engine render → SQL
│   │   5. Convert to pg-trickle stream SQL
│   │   6. Apply generated SQL in a transaction
│   ├── Release exclusive advisory lock
│   ├── Wait for stream tables to be populated (row count > 0 or watermark)
│   └── Update schema_version in component_state
│
├─► Start components:
│   ├── SET desired = 'running' for ingest
│   ├── Wait for ingest health check to report ready
│   ├── SET desired = 'shadow' or 'running' for writeback (per shadow_mode policy)
│   └── (writeback starts after ingest, so initial data is flowing)
│
└─► Enter watch loop:
    ├── inotify watch on /config/ (instant reaction to file sync or ConfigMap remount)
    ├── Periodic config hash check as fallback (detect changes inotify might miss)
    │   └── If changed → re-enter schema diff (stops components first)
    ├── Shadow mode monitoring (check if promotion is pending or auto-promote timer expired)
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

3. **Add component_state polling with advisory lock barrier.** The ingest main loop must check `component_state`, acquire a shared advisory lock for each work cycle, and pause when `desired = 'stopped'`. See the replica work loop pattern in the [Multi-Replica Coordination](#multi-replica-coordination-advisory-lock-barrier) section. This ensures that the schema-manager can wait for all replicas to finish their current cycle before applying DDL.

4. **Remove the wait-for-migrations init container.** The schema-manager handles all ordering. The ingest deployment no longer needs the init container that polls for `alembic_version`.

5. **Health endpoint must report pause state.** When paused, `/ready` should return `503` (not ready to receive traffic) but `/health` should return `200` (process is alive). This allows the schema-manager to distinguish "paused and healthy" from "crashed".

## Changes to In-and-Out Engine (Writeback)

Same pattern as ingest:

1. **Add component_state polling with advisory lock barrier.** Same pattern as ingest — see the [Multi-Replica Coordination](#multi-replica-coordination-advisory-lock-barrier) section. The writeback sync loop checks `desired`, acquires a shared advisory lock for each sync cycle, and pauses when stopped.

2. **Add shadow mode support.** When `desired = 'shadow'`, writeback computes diffs normally but writes them to the `shadow_log` table instead of calling external APIs. It does **not** update `sync_state`, so the same diff is recomputed each cycle until promoted. See the [Shadow Mode](#shadow-mode-for-onboarding) section.

3. **Remove the wait-for-migrations init container.** Same as ingest.

4. **Health endpoint pause-awareness.** `/ready` returns 503 when stopped, 200 when running or in shadow mode. Add a `/mode` endpoint that reports `running`, `shadow`, or `stopped` for observability.

5. **No schema management code.** Writeback already doesn't manage DDL, but it currently assumes views exist on startup. With the component_state gate, this assumption is guaranteed: if writeback is told to run (or shadow), the views are ready.

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
| `shadow` | Writeback only. Computes diffs, writes to `shadow_log`, does not call APIs or update `sync_state`. Health: `/health` → 200, `/ready` → 200 |

### Polling behaviour

Components poll `component_state` every 5 seconds. This is simple, reliable, and has negligible database load.

### Startup behaviour

When a component starts up and finds no row in `component_state` for itself, it inserts one with `desired = 'stopped'` and waits. The schema-manager will set it to `running` when ready.

### Graceful pause

When the schema-manager sets `desired = 'stopped'`, it acquires the relevant exclusive advisory lock(s) (`WRITEBACK_LOCK_KEY` for tier 1 changes, both `INGEST_LOCK_KEY` and `WRITEBACK_LOCK_KEY` for tier 2/3). This blocks until all affected replicas have finished their current work cycle and released their shared locks. No polling of individual replica health endpoints is needed — the locks are the definitive barrier, regardless of how many replicas are running.

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
- Add the schema-manager image build with file sync for dev:
  ```yaml
  - image: sesam-schema-manager
    context: .
    docker:
      dockerfile: docker/schema-manager.Dockerfile
    sync:
      manual:
        - src: "mapping.yaml"
          dest: /config/
        - src: "connectors/**/*.yaml"
          dest: /config/connectors/
        - src: "schema-manager/schema_manager/**/*.py"
          dest: /app/schema_manager/
  ```
  File sync only applies during `skaffold dev`. During `skaffold run` (one-shot deploy) or CI, Skaffold rebuilds the image with baked-in configs.

### Modify: justfile

- Replace `just migrate` recipe: instead of deleting/recreating a K8s Job, it can either:
  - Trigger the schema-manager to re-check configs (e.g. `curl http://localhost:9080/reconcile`)
  - Or for local dev: run the schema-manager CLI directly against localhost Postgres
- Add `just promote` recipe: `curl -X POST http://localhost:9080/promote`

## Config Delivery Strategy

The schema-manager needs `mapping.yaml` and `connectors/*.yaml` to compute the desired database state. How these files reach the schema-manager pod differs between development and production.

### Dev inner loop (`skaffold dev`): file sync + inotify

Config files are baked into the Docker image at build time. During `skaffold dev`, changes are pushed into the running container via Skaffold's file sync (essentially `kubectl cp`) — no image rebuild, no pod restart.

The schema-manager watches `/config/` using `inotify` (via Python's `watchdog` library). When a file changes, it re-hashes immediately and triggers a reconcile if needed.

```
Developer edits mapping.yaml
  → Skaffold detects change         ~1s
  → Skaffold syncs file to pod      ~1s
  → inotify triggers reconcile      instant
                                    ─────
                               Total: ~2s
```

This gives a sub-2-second feedback loop for config changes during development.

### One-shot deploy (`skaffold run`): image rebuild

When using `skaffold run` (or CI pipelines), file sync is not available. Skaffold rebuilds the schema-manager image with the latest configs baked in. The schema-manager starts, reads `/config/`, and reconciles.

```
Developer runs skaffold run
  → Docker builds image (cached layers)  ~10-15s
  → Pod starts with new image
  → Schema-manager reconciles on startup
                                         ───────
                                    Total: ~15-20s
```

### Production: ConfigMap with hash-triggered rollout

In production, configs come from ConfigMaps that override the baked-in files via volume mounts. The kustomize `configMapGenerator` produces name-suffixed ConfigMaps (hash in the name). When config content changes, the ConfigMap name changes, the Deployment's volume reference updates, and Kubernetes rolls out a new pod.

```yaml
# kustomization.yaml — production overlay
configMapGenerator:
  - name: osi-mapping-config
    files:
      - mapping.yaml=../../mapping.yaml
    # No disableNameSuffixHash — hash suffix enables auto-rollout
```

The schema-manager Deployment mounts these ConfigMaps:

```yaml
volumesMounts:
  - name: mapping
    mountPath: /config/mapping.yaml
    subPath: mapping.yaml
  - name: connectors
    mountPath: /config/connectors
volumes:
  - name: mapping
    configMap:
      name: osi-mapping-config
  - name: connectors
    configMap:
      name: inandout-connectors
```

When the ConfigMap hash changes, the new pod starts with the updated files already mounted. The schema-manager's startup reconcile handles the rest.

```
Config change merged to main
  → CI builds + applies new ConfigMap    ~30-60s
  → New pod starts (image cached)         ~5-10s
  → Schema-manager reconciles on startup
                                          ───────
                                     Total: ~40-70s
```

### Dev overlay: remove ConfigMap mounts

The dev overlay strips the ConfigMap volume mounts so that the baked-in files are used and Skaffold file sync works correctly:

```yaml
# k8s/overlays/dev/kustomization.yaml
patches:
  - patch: |-
      - op: remove
        path: /spec/template/spec/volumes/0      # mapping ConfigMap
      - op: remove
        path: /spec/template/spec/volumes/0      # connectors ConfigMap
      - op: remove
        path: /spec/template/spec/containers/0/volumeMounts/0
      - op: remove
        path: /spec/template/spec/containers/0/volumeMounts/0
    target:
      kind: Deployment
      name: sesam-schema-manager
```

### Summary

| Environment | Config source | Trigger mechanism | Latency |
|------------|--------------|-------------------|--------|
| `skaffold dev` | Baked in image + file sync | `inotify` on `/config/` | ~2s |
| `skaffold run` | Baked in image | Startup reconcile | ~15-20s |
| Production | ConfigMap (hash-suffixed) | Pod rollout on CM change | ~40-70s |

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
# Bake config files (overridden by ConfigMap mounts in production)
COPY mapping.yaml /config/mapping.yaml
COPY connectors/ /config/connectors/
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
│   ├── component_gate.py    # Manage component_state table + advisory lock barrier
│   ├── shadow.py            # Shadow mode policy, promotion, shadow_log queries
│   ├── health.py            # /health, /ready, /promote, /metrics endpoints
│   ├── watcher.py           # Watch loop: inotify on /config/ + periodic fallback
│   └── self_upgrade.py      # Version-gated DDL for schema-manager's own tables
```

## Startup Ordering (After Implementation)

```
1. Postgres starts, becomes ready
2. Schema-manager starts, connects to Postgres
3. Schema-manager acquires leader lock
4. Schema-manager runs self-upgrade (component_state, migration_state, shadow_log)
5. Schema-manager sets desired = 'stopped' for ingest and writeback
6. Ingest and writeback start, find desired = 'stopped', idle
7. Schema-manager applies DDL (stubs → Alembic → views → streams)
8. Schema-manager sets desired = 'running' for ingest (can resume during stream rebuild)
9. Schema-manager waits for stream tables to be ready
10. Schema-manager sets desired = 'shadow' or 'running' for writeback (per policy)
11. Writeback begins processing (shadow or live)
```

Steps 5-6 can happen concurrently — ingest and writeback are idling while the schema-manager works. No init containers, no Job ordering, no race conditions.

## Config Change Flow (Steady State)

```
1. Developer changes mapping.yaml (e.g. adds a field)
2. skaffold dev detects change, rebuilds ConfigMap, rolls out
3. Schema-manager's watch loop detects config hash change
4. Schema-manager sets desired = 'stopped' for writeback (and ingest if tier 2/3)
5. Schema-manager acquires exclusive advisory lock(s) for affected components
6. Schema-manager drops and recreates generated views + streams
7. Schema-manager releases exclusive advisory lock(s)
8. Schema-manager waits for stream tables to rebuild
9. Schema-manager sets desired = 'running' for ingest
10. Schema-manager sets desired = 'shadow' or 'running' for writeback (per shadow_mode policy)
```

If only mapping.yaml changed (no new sources), this is a tier 1 change: ingest can keep running during steps 4-8 — it writes to staging tables that are unaffected by view recreation. Only writeback must be stopped to prevent it from reading partially-rebuilt views.

When `shadow_mode.on_change` is `always`, writeback enters shadow mode after every config change. The operator reviews `shadow_log` and runs `schema-manager promote` to go live. When the policy is `never`, writeback goes straight to `running`.

## Change Classification

Not all config changes have the same impact. The schema-manager classifies changes into tiers to minimise disruption:

| Tier | Trigger | What stops | What keeps running |
|------|---------|-----------|-------------------|
| **Tier 0: No-op** | Config hash unchanged | Nothing | Everything |
| **Tier 1: Views only** | mapping.yaml field/strategy change (no new sources) | Writeback only | Ingest |
| **Tier 2: Sources + views** | New source in mapping.yaml, or new connector entity | Both ingest and writeback | — |
| **Tier 3: Operational schema** | New Alembic revision (in-and-out upgrade) | Both ingest and writeback | — |

To support tiered pausing, ingest and writeback use **separate advisory lock keys**:

```python
INGEST_LOCK_KEY    = 0x5E5A_0001
WRITEBACK_LOCK_KEY = 0x5E5A_0002
```

The schema-manager acquires only the locks it needs:
- Tier 1: acquire `WRITEBACK_LOCK_KEY` only
- Tier 2/3: acquire both `INGEST_LOCK_KEY` and `WRITEBACK_LOCK_KEY`

The classification is determined by diffing the old and new config:
- If only `mappings:` or `targets:` sections changed → Tier 1
- If `sources:` section changed or new connector entities appeared → Tier 2
- If the Alembic head revision changed → Tier 3

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| Postgres unreachable | Schema-manager retries with exponential backoff. Components stay stopped. |
| osi-engine render fails | Schema-manager logs error, does not apply partial DDL, components stay stopped. Retry on next watch cycle. |
| Alembic upgrade fails | Same — log, skip, retry. |
| Generated SQL apply fails | Transaction rolls back. Old views remain (if this is a re-deploy) or no views exist (first deploy). Components stay stopped. |
| Schema-manager crashes | Components remain in their last `desired` state. Exclusive advisory lock is auto-released (session closes). If components were running, they continue (safe — schema hasn't changed). On restart, re-enters reconcile loop. |
| Component replica crashes | Shared advisory lock auto-released (session closes). Schema-manager is not blocked. Kubernetes restartPolicy handles the restart; new replica will check `component_state` on startup. |

## Rollback Strategy

The checksum-gated approach means a bad `mapping.yaml` (e.g., wrong merge strategy) can produce technically valid but semantically wrong golden records. If writeback is enabled, those wrong records get pushed to production APIs before anyone notices.

Mitigations:

1. **Store previous generated SQL.** The schema-manager stores the last-known-good generated SQL (matviews.sql content) in the `migration_state` table as a `previous_sql` entry. If a rollback is needed, the operator can invoke `schema-manager rollback` which restores the previous SQL and re-applies it.

2. **Dry-run mode.** The schema-manager supports a `--dry-run` flag (and `/reconcile?dry_run=true` endpoint) that computes and logs the diff without applying DDL or restarting components. This allows operators to preview what a config change will do.

3. **Git is the source of truth.** Since `mapping.yaml` is version-controlled, a `git revert` followed by redeployment is always an option. The schema-manager will detect the hash change and re-apply. Combined with the checksum gate, this is fast.

4. **Extended shadow mode for onboarding.** New mapping configurations are validated through shadow mode before writeback pushes to production APIs. See the [Shadow Mode](#shadow-mode-for-onboarding) section. This is integrated into the schema-manager: after a config change, writeback enters shadow mode automatically (per policy), computes diffs without calling APIs, and logs them in `shadow_log` for operator review. Only after explicit promotion does writeback go live.

## Database Users and Privilege Separation

The plan designates the schema-manager as the only DDL writer, but all components currently connect as the same `inandout` PostgreSQL user. To enforce this guarantee, use separate database users:

| User | Used by | Privileges |
|------|---------|------------|
| `sesam_admin` | schema-manager | `CREATE`, `ALTER`, `DROP` on all schemas. Full DDL. Owns all tables and views. |
| `sesam_ingest` | ingest replicas | `INSERT`, `UPDATE` on `inout_src_*` staging tables. `SELECT` on `component_state`. No DDL. |
| `sesam_writeback` | writeback replicas | `SELECT` on generated views, `SELECT`/`INSERT`/`UPDATE` on `sync_state`, `sync_task`, `cross_system_link`, `shadow_log`. `SELECT` on `component_state`. No DDL. |

This prevents a bug in ingest from accidentally altering tables, and makes the single-owner guarantee enforceable at the database level rather than by convention alone.

Implementation:
- The schema-manager creates these users and grants privileges as part of its DDL application step
- The `sesam-credentials` Secret gains additional DSN entries (`INGEST_DATABASE_URL`, `WRITEBACK_DATABASE_URL`) with the restricted users
- The `inandout-config` ConfigMap references the appropriate DSN per component
- Advisory locks work across users — a shared lock acquired by `sesam_ingest` is visible to `sesam_admin`'s exclusive lock request

## Shadow Mode for Onboarding

When a new mapping configuration is deployed, writeback enters **shadow mode** before going live. In shadow mode, writeback computes what it would push to external APIs but writes the diffs to a log table for operator review instead. No data leaves the system until an operator explicitly promotes the config.

### Shadow log table

```sql
CREATE TABLE IF NOT EXISTS shadow_log (
    id              BIGSERIAL PRIMARY KEY,
    cycle_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version  TEXT NOT NULL,
    target_system   TEXT NOT NULL,         -- 'hubspot', 'tripletex'
    entity_type     TEXT NOT NULL,         -- 'person', 'company'
    operation       TEXT NOT NULL,         -- 'create', 'update', 'delete'
    record_id       TEXT,                  -- golden_id or external_id
    diff            JSONB NOT NULL,        -- full payload that would be sent
    suppressed      BOOLEAN DEFAULT FALSE  -- operator can mark false positives
);
```

This table is created by the schema-manager as part of its DDL step and owned by `sesam_admin`. The `sesam_writeback` user has `INSERT` and `SELECT` privileges.

### Writeback behaviour in shadow mode

```python
state = db.execute(
    "SELECT desired FROM component_state WHERE component = 'writeback'"
).scalar()

if state == 'stopped':
    time.sleep(5)
    continue

# Both 'running' and 'shadow' acquire the lock and compute diffs
db.execute("SELECT pg_advisory_lock_shared(%s)", [WRITEBACK_LOCK_KEY])
try:
    diffs = compute_sync_diffs()

    if state == 'shadow':
        write_to_shadow_log(diffs, schema_version)
        # Do NOT call APIs, do NOT update sync_state
    elif state == 'running':
        execute_sync(diffs)       # call APIs
        update_sync_state(diffs)  # record what was written
finally:
    db.execute("SELECT pg_advisory_unlock_shared(%s)", [WRITEBACK_LOCK_KEY])
```

Because `sync_state` is not updated in shadow mode, the same diff is recomputed on each cycle. This is intentional — it ensures the shadow log reflects the current state of the world, not a stale snapshot.

### Reviewing the shadow log

Operators query the shadow log to validate the new config before promoting:

```sql
-- Summary: how many changes would this config push?
SELECT target_system, operation, count(*)
FROM shadow_log
WHERE schema_version = 'sha256:abc123...'
GROUP BY target_system, operation;

-- Show all deletes (the most dangerous operations)
SELECT * FROM shadow_log
WHERE operation = 'delete' AND schema_version = 'sha256:abc123...';

-- Mark false positives
UPDATE shadow_log SET suppressed = TRUE WHERE id IN (...);
```

### Onboarding flow

```
1. Developer deploys new or modified mapping.yaml

2. Schema-manager detects config change
   ├── Pauses writeback, applies DDL
   ├── Starts ingest with desired = 'running'
   └── Starts writeback with desired = 'shadow'   ← per shadow_mode policy

3. Writeback runs in shadow mode
   ├── Computes diffs each cycle
   ├── Writes to shadow_log
   └── Does NOT call APIs or update sync_state

4. Operator reviews shadow_log
   ├── Checks for unexpected deletes / excessive change volume
   ├── Validates field values look correct
   └── Decides: promote or rollback?

5a. Promote → operator runs: schema-manager promote
    ├── Schema-manager sets writeback desired = 'running'
    ├── Writeback starts pushing to APIs normally
    └── shadow_log preserved for audit (or truncated)

5b. Rollback → operator reverts mapping.yaml, redeploys
    ├── Schema-manager detects hash change, re-applies old schema
    ├── shadow_log shows what would have happened (post-mortem)
    └── No data was pushed — production APIs are untouched
```

### Promotion

The schema-manager exposes a `/promote` endpoint and CLI command:

```bash
# Via HTTP
curl -X POST http://schema-manager:9080/promote

# Via CLI (local dev)
schema-manager promote --database "$INOUT_DATABASE_URL"

# Via justfile
just promote
```

Promotion sets `desired = 'running'` for writeback only if it is currently in `shadow` state. If writeback is already running or stopped, the command is a no-op and logs a warning.

### Shadow mode policy

The schema-manager config determines when shadow mode is used:

```yaml
# schema-manager config
shadow_mode:
  on_change: always          # 'always' | 'new_targets_only' | 'never'
  auto_promote_after: null   # optional: auto-promote after duration (e.g. '24h')
```

| Policy | Behaviour |
|--------|-----------|
| `always` | Every config change puts writeback into shadow. Promotion always manual. Recommended for onboarding and production. |
| `new_targets_only` | Shadow for tier 2/3 changes (new sources/targets). Tier 1 changes (field/strategy on existing targets) go straight to live. For mature deployments. |
| `never` | Writeback always goes straight to live. For development and testing only. |

If `auto_promote_after` is set, the schema-manager's watch loop checks whether the shadow period has elapsed and no anomalies were detected (e.g., delete count below threshold, change volume within historical norms). If conditions are met, it promotes automatically. This is a future enhancement — manual promotion is the default.

## Config Hash Scope

The config hash determines whether a reconcile triggers DDL changes. It must cover only **schema-relevant** inputs — otherwise unrelated config changes (rate limits, timeouts, auth tokens) would unnecessarily pause components and re-apply DDL.

The hash covers:
- `mapping.yaml` — full file (any change may affect views)
- `connectors/*.yaml` — **only** the `entities:` section (which tables to create). The `connection`, `auth`, `rate_limit`, `retry`, `circuit_breaker`, and `webhooks` sections are excluded.
- Alembic revision head (from in-and-out engine)

The schema-manager extracts the schema-relevant portions before hashing:

```python
def compute_config_hash(mapping_path, connector_paths, alembic_head):
    h = hashlib.sha256()
    h.update(Path(mapping_path).read_bytes())
    for path in sorted(connector_paths):
        connector = yaml.safe_load(Path(path).read_text())
        # Hash only entities, not connection/auth/rate_limit
        entities = connector.get("connector", {}).get("entities", {})
        h.update(yaml.dump(entities, sort_keys=True).encode())
    h.update(alembic_head.encode())
    return f"sha256:{h.hexdigest()}"
```

This means changing a rate limit or API token does **not** trigger a schema reconcile. Only adding/removing entities or changing the mapping triggers DDL.

## Observability

The schema-manager is the most critical component — if it's broken, nothing works. It needs comprehensive observability.

### Metrics (Prometheus)

Exposed on the health port (9080) at `/metrics`:

| Metric | Type | Description |
|--------|------|-------------|
| `schema_manager_reconcile_total` | Counter | Total reconcile attempts, labelled by `tier` and `result` (success/failure) |
| `schema_manager_reconcile_duration_seconds` | Histogram | Time spent in each reconcile cycle |
| `schema_manager_component_desired_state` | Gauge | Current `desired` state per component (0=stopped, 1=running, 2=shadow) |
| `schema_manager_migration_gate_held_seconds` | Histogram | How long the exclusive advisory lock was held |
| `schema_manager_shadow_log_size` | Gauge | Number of unsuppressed rows in `shadow_log` |
| `schema_manager_config_hash` | Info | Current config hash (for label joins) |
| `schema_manager_last_reconcile_timestamp` | Gauge | Unix timestamp of last successful reconcile |
| `schema_manager_stream_rebuild_duration_seconds` | Histogram | Time waiting for stream tables to populate after DDL |

### Structured logging

Every lifecycle step emits a JSON log event with:
- `event`: step name (e.g. `reconcile_start`, `ddl_apply`, `component_resume`, `shadow_enter`)
- `tier`: change classification tier
- `config_hash`: current hash
- `duration_ms`: elapsed time for the step
- `error`: error message if the step failed

Example:
```json
{"event": "reconcile_start", "tier": 1, "config_hash": "sha256:abc123", "ts": "2026-03-31T12:00:00Z"}
{"event": "ddl_apply", "tier": 1, "duration_ms": 342, "config_hash": "sha256:abc123"}
{"event": "component_resume", "component": "writeback", "desired": "shadow"}
```

### Alerting rules (Prometheus/Alertmanager)

```yaml
# Schema-manager has not reconciled successfully in 10 minutes
- alert: SchemaManagerReconcileStale
  expr: time() - schema_manager_last_reconcile_timestamp > 600
  for: 5m
  labels:
    severity: warning

# Components stuck in stopped state for more than 5 minutes
- alert: ComponentsStuckStopped
  expr: schema_manager_component_desired_state == 0
  for: 5m
  labels:
    severity: critical

# Shadow log growing without promotion
- alert: ShadowLogUnreviewed
  expr: schema_manager_shadow_log_size > 1000
  for: 1h
  labels:
    severity: warning
```

## Graceful Shutdown

When the schema-manager pod is terminated (rolling update, node drain, `SIGTERM`), it must shut down cleanly:

1. **Stop the watch loop** — no new reconcile cycles.
2. **If mid-migration**: the DDL transaction rolls back automatically (Postgres). The exclusive advisory lock is released when the session closes. Components remain in `desired = 'stopped'` — the new instance will detect this and resume the reconcile.
3. **Do NOT set `desired = 'stopped'` on shutdown.** The current component states should be preserved. If components are running, the new schema-manager instance will start, acquire the leader lock, check the hash, find no changes, and leave them running.
4. **Release the leader lock explicitly** via `pg_advisory_unlock(LEADER_LOCK_KEY)` in the shutdown handler. This allows the replacement instance to acquire it immediately instead of waiting for the session timeout.
5. **Close database connections cleanly** to avoid leaked sessions.

```python
import signal

def shutdown_handler(signum, frame):
    log.info("Shutting down...")
    watch_loop.stop()
    if leader_lock_held:
        db.execute("SELECT pg_advisory_unlock(%s)", [LEADER_LOCK_KEY])
    db.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)
```

Kubernetes sends `SIGTERM` and waits `terminationGracePeriodSeconds` (default 30s) before `SIGKILL`. The schema-manager should complete shutdown well within this window since there's no long-running work to finish (any in-flight migration is rolled back by the transaction).

## Long Stream Rebuilds

After dropping and recreating generated views and stream tables, pg-trickle must rebuild the stream tables from scratch. On large datasets this can take minutes or hours. During this time, the plan currently keeps both ingest and writeback paused.

For the PoC, this is acceptable — datasets are small. For production, two mitigations:

### Allow ingest to resume during stream rebuilds

Ingest writes to staging tables, which are unaffected by view/stream recreation. The schema-manager can release the ingest advisory lock and set `desired = 'running'` for ingest immediately after DDL is applied — before streams are fully populated. pg-trickle will incrementally process the new data as it arrives alongside the initial backfill.

Writeback remains paused until streams are ready, because it reads from the generated views which depend on stream tables.

This reduces the disruption window for ingest from "full rebuild time" to "DDL apply time" (~seconds).

### Stream readiness detection

The schema-manager needs to know when stream tables are "ready enough" for writeback to resume. Options:

1. **Row count comparison**: compare stream table row count to source staging table count. When they converge (within a threshold), streams are ready. Simple but imprecise for tables with deletes.
2. **pg-trickle watermark**: if pg-trickle exposes an LSN or progress indicator, the schema-manager can wait for it to reach the current WAL position.
3. **Timeout with verification**: wait a configurable duration (e.g. 60s for PoC), then spot-check a sample of golden records against expected values.

For the PoC, option 3 (timeout) is simplest. Option 2 is preferred long-term if pg-trickle supports it.

## Schema-Manager Configuration

The schema-manager's own configuration lives in a YAML file at `/config/schema-manager.yaml`:

```yaml
# schema-manager.yaml
database:
  dsn: "${INOUT_DATABASE_URL}"

config_paths:
  mapping: /config/mapping.yaml
  connectors: /config/connectors/

health_server:
  listen: "0.0.0.0:9080"

watch:
  poll_interval: 30          # seconds between fallback hash checks
  stream_ready_timeout: 60   # seconds to wait for stream tables after DDL

shadow_mode:
  on_change: always          # 'always' | 'new_targets_only' | 'never'
  auto_promote_after: null   # e.g. '24h' to auto-promote

observability:
  logging:
    format: json
    level: info
  metrics:
    enabled: true
```

This file is baked into the Docker image and can be overridden by a ConfigMap mount in production. Environment variable substitution (e.g. `${INOUT_DATABASE_URL}`) is supported for secrets.

The dev overlay can set `shadow_mode.on_change: never` to skip shadow mode during local development.

## Testing Strategy

The schema-manager is complex enough to require tests at three levels:

### Unit tests

Pure Python tests (no database) for:
- Config hash computation (verify that non-schema fields are excluded)
- Change tier classification (given old and new config, verify correct tier)
- Stub DDL generation (given `sources:` section, verify correct `CREATE TABLE` SQL)
- Shadow mode policy logic (given tier and policy, verify correct desired state)

### Integration tests (testcontainers)

Spin up a real Postgres (with pg-trickle) via testcontainers-python:
- Full reconcile cycle: apply stubs → Alembic → views → streams → verify all objects exist
- Config change: modify mapping, reconcile again, verify views are updated
- Advisory lock barrier: start multiple mock "replicas" holding shared locks, verify schema-manager blocks until they release
- Shadow mode: verify writeback enters shadow, `shadow_log` is populated, promotion works
- Rollback: verify `schema-manager rollback` restores previous SQL
- Leader election: start two schema-manager instances, verify only one acquires the lock

### End-to-end tests (kind cluster)

Full Kubernetes deployment:
- Deploy from scratch, verify startup ordering (schema-manager → ingest → writeback)
- Change `mapping.yaml`, trigger Skaffold file sync, verify reconcile completes in ~2s
- Scale writeback to 3 replicas, trigger config change, verify all replicas pause before DDL
- Promote from shadow to live, verify writeback starts pushing to simulator
- Kill schema-manager pod, verify components continue running, new instance takes over

### Test location

```
schema-manager/
├── tests/
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_tiers.py
│   │   ├── test_stubs.py
│   │   └── test_shadow_policy.py
│   ├── integration/
│   │   ├── conftest.py          # testcontainers fixtures
│   │   ├── test_reconcile.py
│   │   ├── test_advisory_locks.py
│   │   ├── test_shadow_mode.py
│   │   └── test_leader_election.py
│   └── e2e/
│       └── test_full_deploy.py  # requires kind cluster
```

## Schema-Manager Self-Upgrade

The schema-manager manages everyone else's schema, but what about its own tables (`component_state`, `migration_state`, `shadow_log`)? When a new schema-manager version needs to change these tables, it needs its own migration path.

Approach: **version-gated idempotent DDL** at startup.

The schema-manager stores its own version in `migration_state`:

```sql
-- ('schema_manager_version', '1')  -- current internal schema version
```

On startup, the schema-manager checks its internal version and applies upgrade steps if needed:

```python
INTERNAL_VERSION = 2  # bump when component_state/migration_state/shadow_log schema changes

def self_upgrade(db):
    current = db.execute(
        "SELECT value FROM migration_state WHERE key = 'schema_manager_version'"
    ).scalar() or "0"

    if int(current) < 1:
        # v1: initial schema
        db.execute(CREATE_COMPONENT_STATE)
        db.execute(CREATE_MIGRATION_STATE)

    if int(current) < 2:
        # v2: add shadow_log table
        db.execute(CREATE_SHADOW_LOG)

    db.execute(
        "INSERT INTO migration_state (key, value) VALUES ('schema_manager_version', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        [str(INTERNAL_VERSION)]
    )
```

This runs before any other reconcile logic, so the schema-manager's own tables are always up to date. Each upgrade step is idempotent (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). No external migration tool is needed — the schema-manager bootstraps itself.

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

1. **Create the `schema-manager/` Python package** with the reconciler, config reader, stub generator, osi-engine caller, SQL applier, component gate, and shadow mode controller.
2. **Create the Dockerfile** (`docker/schema-manager.Dockerfile`) as described above.
3. **Create `k8s/base/schema-manager.yaml`** Deployment manifest.
4. **Add component_state polling** to the in-and-out engine (ingest and writeback commands). This is a code change in `vendor/in-and-out`.
5. **Add shadow mode support to writeback** — branch on `desired = 'shadow'` to write `shadow_log` instead of calling APIs. Code change in `vendor/in-and-out`.
6. **Disable dlt schema creation** in the ingest pipeline config. Change in `vendor/in-and-out`.
7. **Remove `wait-for-migrations` init containers** from `ingest.yaml` and `writeback.yaml`.
8. **Delete `migrate-job.yaml`** and update `kustomization.yaml`.
9. **Update `skaffold.yaml`**: add schema-manager image build, remove Job delete hook.
10. **Update `justfile`**: replace Job-based migrate recipe, add `just promote` recipe.
11. **Test**: deploy to kind cluster, verify startup ordering, config-change flow, shadow mode, and promotion.

## Open Questions

- Should the schema-manager expose a `/reconcile` HTTP endpoint for on-demand triggers, or rely purely on inotify + periodic fallback?
- Does pg-trickle expose an LSN or progress indicator for stream table readiness? If not, which fallback (row count comparison, timeout, or spot-check) should be the default?
- Should auto-promotion be supported (promote after N hours with no anomalies), or should promotion always be manual?
