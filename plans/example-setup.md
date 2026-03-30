# Example Setup: Two-Way Sync Between HubSpot and Tripletex

## Goal

Build a working end-to-end example that syncs **companies**, **contacts**, and the **relationship between them** bidirectionally between HubSpot and Tripletex. The example exercises the full stack:

- **[in-and-out](https://github.com/grove/in-and-out)** — ingest, writeback, and simulators for both systems
- **[OSI-mapping](https://github.com/BaardBouvet/OSI-mapping)** — declarative mapping config that merges both systems through a common model
- **[pg-trickle](https://github.com/grove/pg-trickle)** — IVM in PostgreSQL to keep derived views incrementally up to date

## What We Need to Produce

| # | Artifact | Repo | Status | Description |
|---|----------|------|--------|-------------|
| 1 | `connectors/hubspot.yaml` | in-and-out | Needs expansion | HubSpot connector — contacts, companies, associations (incl. assoc writeback) |
| 2 | `connectors/tripletex.yaml` | in-and-out | Needs expansion | Tripletex connector — contacts, customers (incl. incremental contacts) |
| 3 | `mapping.yaml` | OSI-mapping / this repo | Mostly done | Single mapping file covering all entities + tests |

The existing `*.example.yaml` connectors in [in-and-out/connectors/](https://github.com/grove/in-and-out/tree/main/connectors) are **stubs that need material expansion** — missing association writeback, incremental contact ingestion, and seed data alignment. The mapping in [mappings/mapping.yaml](../mappings/mapping.yaml) covers the scenario structurally but needs validation against real in-and-out table schemas.

## Architecture

```
┌──────────────────┐                                    ┌──────────────────┐
│  HubSpot         │                                    │  Tripletex       │
│  (simulator)     │                                    │  (simulator)     │
│                  │                                    │                  │
│  contacts        │                                    │  contacts        │
│  companies       │                                    │  customers       │
│  associations    │                                    │  (contact→cust)  │
└────────┬─────────┘                                    └────────┬─────────┘
         │                                                       │
    ┌────▼────┐                                             ┌────▼────┐
    │ in-and- │   INGEST (pull)                             │ in-and- │
    │ out     │──────────────┐           ┌──────────────────│ out     │
    │ hubspot │              │           │                  │ triplet. │
    │connector│◀─────────────┤           ├─────────────────▶│connector│
    └─────────┘   WRITEBACK  │           │   WRITEBACK      └─────────┘
                  (push)     │           │   (push)
                             ▼           ▼
                    ┌────────────────────────────┐
                    │      PostgreSQL 18          │
                    │                             │
                    │  ┌─ inandout_hubspot ─────┐ │
                    │  │  contacts              │ │
                    │  │  companies             │ │
                    │  │  associations          │ │
                    │  └────────────────────────┘ │
                    │                             │
                    │  ┌─ inandout_tripletex ───┐ │
                    │  │  contacts              │ │
                    │  │  customers             │ │
                    │  └────────────────────────┘ │
                    │                             │
                    │  ┌─ osi_mapping (views) ──┐ │  ← OSI-mapping engine
                    │  │  fwd_hubspot_contacts  │ │    generates these SQL
                    │  │  fwd_tripletex_contacts│ │    views from mapping.yaml
                    │  │  identity_person       │ │
                    │  │  resolved_person       │ │
                    │  │  resolved_company      │ │
                    │  │  resolved_assoc        │ │
                    │  │  rev_hubspot_contacts  │ │
                    │  │  rev_tripletex_contacts│ │
                    │  │  delta_*               │ │
                    │  └────────────────────────┘ │
                    │                             │
                    │  ┌─ pg_trickle ───────────┐ │  ← stream tables for
                    │  │  (IVM over the views)  │ │    incremental refresh
                    │  └────────────────────────┘ │
                    └────────────────────────────┘
```

## Data Model Differences

The key challenge is that the contact-to-company relationship is modeled differently in the two systems:

| Concept | HubSpot | Tripletex |
|---------|---------|-----------|
| Company entity | `companies` (standalone) | `customers` (standalone) |
| Contact entity | `contacts` (standalone) | `contacts` (standalone) |
| Relationship | Separate `associations` API (many-to-many, typed) | `customer.id` FK embedded on contact (many-to-one) |
| Association identity | `(fromObjectId, toObjectId)` compound key | Implicit: `(contact.id, contact.customer.id)` |

The mapping handles this by:

1. Mapping both HubSpot companies and Tripletex customers → `company` target (identity: `org_number`)
2. Mapping both HubSpot contacts and Tripletex contacts → `person` target (identity: `email`)
3. HubSpot associations → `person_company_association` (explicit source)
4. Tripletex contacts → `person_company_association` (embedded, filtered on `customer_id IS NOT NULL`)

## Detailed Plan

### Phase 1: Connectors (in-and-out)

The existing example connectors are **stubs** — they demonstrate the YAML structure but need significant expansion before they can drive this scenario.

#### HubSpot Connector — Gaps

| Area | Current state | Needed |
|------|--------------|--------|
| contacts ingestion | ✅ cursor pagination, incremental, writeback | OK |
| companies ingestion | ✅ cursor pagination, incremental, writeback | OK |
| associations ingestion | ✅ batch read, cursor pagination | OK |
| **associations writeback** | ❌ missing | POST/DELETE to `/crm/v3/associations/contacts/companies/batch/create` and `.../archive` — needed for TT→HS relationship sync |
| **simulator: association seed** | Partial — 1 record | Expand to cover the merge scenario (multiple contacts linked to companies) |
| **simulator: cross-ref alignment** | Seed contact `1001` links to company `2001` | Need at least one shared contact (same email in both systems) to test merge |

#### Tripletex Connector — Gaps

| Area | Current state | Needed |
|------|--------------|--------|
| customers ingestion | ✅ offset pagination, incremental, writeback | OK |
| contacts ingestion | ⚠️ offset pagination, **no incremental** | Add `changedSince` cursor (same pattern as customers) |
| contacts writeback | ✅ PUT | OK |
| **contact→customer FK** | Implicit in seed data (`customer.id` on contact) | Verify this is ingested as a column and visible to OSI-mapping |
| **simulator: cross-ref alignment** | Seed contacts link to customers | Need at least one shared contact (same email as HubSpot) to test merge |

#### Simulator Seed Data Alignment

The simulators must produce data that exercises the **merge, create-in-other-system, and relationship-sync** paths:

| Entity | HubSpot seed | Tripletex seed | Merge key |
|--------|-------------|----------------|-----------|
| Company: Acme | `id: "2001"`, name: "Acme Corp", domain: acme.com | `id: 10001`, name: "Acme AS", orgNum: 912345678 | `org_number: 912345678` |
| Contact: Alice | `id: "1001"`, email: alice@example.com | `id: 20001`, email: alice@example.com | email match → merged person |
| Contact: Bob | `id: "1002"`, email: bob@example.com | — | HubSpot-only → should be created in TT |
| Contact: Kari | — | `id: 20002`, email: kari@acme.no | TT-only → should be created in HS |
| Assoc: Alice→Acme | `1001 → 2001` (contact_to_company) | contact 20001 has `customer.id: 10001` | Both systems record Alice→Acme → single merged association |
| Assoc: Bob→Acme | `1002 → 2001` | — | Should create `customer.id` on Bob's TT contact after writeback |
| Assoc: Kari→Acme | — | contact 20002 has `customer.id: 10001` | Should create HS association after writeback |

### Phase 2: OSI-Mapping Config

The existing `mappings/mapping.yaml` defines the correct entity structure. It does **not** need a `country` target or `country_seed` source for the initial example — that was scaffolded for future reference-data vocabulary mapping (e.g. Tripletex "Norge" ↔ HubSpot "Norway" ↔ ISO "NO"). We can drop it from the example and add it later if/when we need country normalization.

**Simplified targets for the example:**
- `person` — merged contacts (identity: `email`)
- `company` — merged companies (identity: `org_number`, link_group: `domain`)
- `person_company_association` — unified relationships (identity: `person_id` + `company_id` via references)

**Resolution rules:**
- Person name: HubSpot wins (priority 1); Tripletex fallback (priority 2)
- Person address: Tripletex wins (priority 1); HubSpot fallback (priority 2)
- Person phone: last-modified wins
- Company name: HubSpot wins (priority 1)
- Company address: Tripletex wins (priority 1)
- Company phone: last-modified wins

**What the OSI-mapping engine generates** from this config:
1. **Forward views** — per-mapping views that normalize source fields to target shape
2. **Identity views** — match records across sources (email match for persons, org_number for companies)
3. **Resolution views** — apply coalesce/last_modified/expression strategies to pick winning values
4. **Reverse views** — map resolved golden record back to each source's field names
5. **Delta views** — detect differences between current source state and desired state

### Phase 3: pg-trickle IVM

The OSI-mapping engine can output **materialized views** via a CLI switch. pg-trickle provides a conversion script ([`scripts/convert_matviews_to_pgtrickle.py`](https://github.com/grove/pg-trickle/blob/main/scripts/convert_matviews_to_pgtrickle.py)) that rewrites `CREATE MATERIALIZED VIEW` → `pgtrickle.create_stream_table()` and `REFRESH MATERIALIZED VIEW` → `pgtrickle.refresh_stream_table()`.

**Pipeline:**

```
mapping.yaml
    │
    ▼  osi-mapping engine --materialized-views
setup_matviews.sql          (CREATE MATERIALIZED VIEW …)
    │
    ▼  convert_matviews_to_pgtrickle.py
setup_stream_tables.sql     (SELECT pgtrickle.create_stream_table(…))
    │
    ▼  psql -f setup_stream_tables.sql
PostgreSQL (stream tables registered, DAG built, IVM active)
```

pg-trickle handles the rest: trigger-based CDC on ingested tables, DAG-aware topological refresh, and IMMEDIATE or scheduled mode per stream table.

With IMMEDIATE mode, the stream tables update within the same transaction as the ingested data:

1. in-and-out writes a new HubSpot contact → forward view updates → identity resolution runs → golden record updates → reverse views update → delta views detect changes — all in-transaction
2. in-and-out writeback polls delta views for pending changes → pushes to target APIs

### Phase 4: Deployment & Runtime

#### Decision: Skaffold + Git Submodules

We use **Skaffold + K8s** (matching ADR-003) with the three dependency repos as **git submodules** in this workspace. This gives us:

1. **Single workspace** — edit connectors, mapping config, and engine code in one place without switching repos
2. **Skaffold hot-reload** — change a connector YAML or Python source → Skaffold rebuilds + redeploys the affected pod in seconds
3. **Fix-forward workflow** — when hitting an integration seam issue (G4, G5), fix it directly in the submodule, test locally, then push upstream
4. **Production-representative** — tests the real K8s topology (separate pods, services, CNPG for Postgres)

#### Git Submodule Layout

```
sesam-opensource-poc/
├── vendor/
│   ├── in-and-out/        ← git submodule: github.com/grove/in-and-out
│   ├── pg-trickle/        ← git submodule: github.com/grove/pg-trickle
│   └── osi-mapping/       ← git submodule: github.com/BaardBouvet/OSI-mapping
├── k8s/                   ← K8s manifests for this example (references submodules)
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   ├── postgres.yaml       # CNPG Cluster or StatefulSet with pg_trickle
│   │   ├── simulator.yaml
│   │   ├── migrate-job.yaml    # Alembic + OSI-mapping SQL + stream table setup
│   │   ├── ingest.yaml
│   │   ├── writeback.yaml
│   │   └── configmap.yaml      # Connector YAMLs mounted from connectors/
│   └── overlays/
│       └── dev/
│           └── kustomization.yaml  # Dev-specific: NodePort, resource limits, etc.
├── skaffold.yaml
├── connectors/
│   ├── hubspot.yaml
│   └── tripletex.yaml
├── mappings/
│   └── mapping.yaml
└── ...
```

#### Setting Up Submodules

```bash
git submodule add https://github.com/grove/in-and-out.git vendor/in-and-out
git submodule add https://github.com/grove/pg-trickle.git vendor/pg-trickle
git submodule add https://github.com/BaardBouvet/OSI-mapping.git vendor/osi-mapping
```

When working on fixes, create a branch in the submodule, commit, then push upstream:
```bash
cd vendor/in-and-out
git checkout -b fix/association-writeback
# ... make changes ...
git commit && git push
cd ../..
git add vendor/in-and-out
git commit -m "bump in-and-out to include association writeback fix"
```

#### Skaffold Configuration

```yaml
# skaffold.yaml
apiVersion: skaffold/v4beta11
kind: Config
metadata:
  name: sesam-poc

build:
  artifacts:
    - image: inandout-engine
      context: vendor/in-and-out
      docker:
        dockerfile: engine/Dockerfile
    - image: inandout-simulator
      context: vendor/in-and-out
      docker:
        dockerfile: simulator/Dockerfile
    - image: osi-mapping-engine
      context: vendor/osi-mapping
      docker:
        dockerfile: engine-rs/Dockerfile       # or a thin wrapper
  local:
    push: false                                 # minikube/kind — no registry needed

deploy:
  kustomize:
    paths:
      - k8s/overlays/dev

portForward:
  - resourceType: service
    resourceName: postgres
    port: 5432
    localPort: 5432
  - resourceType: service
    resourceName: simulator
    port: 6100
    localPort: 6100
  - resourceType: service
    resourceName: ingest
    port: 9090
    localPort: 9090
  - resourceType: service
    resourceName: writeback
    port: 9091
    localPort: 9091
```

Skaffold watches `vendor/in-and-out/`, `vendor/osi-mapping/`, `connectors/`, and `mappings/` for changes. Editing a connector YAML triggers a configmap update + pod restart. Editing Python engine code triggers a full image rebuild + redeploy.

#### Local K8s Cluster

Requires a local Kubernetes cluster. Recommended: **minikube** or **kind**.

```bash
# One-time setup
minikube start --cpus=4 --memory=8g --kubernetes-version=v1.33.0
minikube addons enable ingress

# Run (Skaffold handles build + deploy + port-forward + log tailing)
skaffold dev
```

`skaffold dev` enters a continuous loop: build → deploy → tail logs → watch for changes → rebuild. `Ctrl+C` tears down all deployed resources.

#### PostgreSQL with pg-trickle

Two options for PG 18 + pg_trickle in K8s:

| Option | How | When to use |
|--------|-----|-------------|
| **CNPG + extension image** | CloudNativePG `Cluster` resource with `ghcr.io/grove/pg_trickle-ext:0.11.0` as image volume extension | Closest to production. Requires CNPG operator installed in cluster. |
| **Custom StatefulSet** | Build from `vendor/pg-trickle/Dockerfile.hub` which bundles PG 18 + pg_trickle | Simpler. No operator dependency. Good for dev. |

Start with the **StatefulSet** approach for simplicity. Add the CNPG path later.

```yaml
# k8s/base/postgres.yaml (StatefulSet approach)
# Image built from vendor/pg-trickle/Dockerfile.hub
# Mounts: shared_preload_libraries = 'pg_trickle'
```

This gets added to `skaffold.yaml` as another build artifact:
```yaml
    - image: postgres-pgtrickle
      context: vendor/pg-trickle
      docker:
        dockerfile: Dockerfile.hub
```

#### Runtime Architecture

```
skaffold dev
├── postgres-pgtrickle    (PG 18 + pg_trickle, port-forwarded to :5432)
├── simulator             (FastAPI — HubSpot + Tripletex, port-forwarded to :6100)
├── migrate               (Job: alembic + osi-mapping SQL + stream tables — runs once)
├── ingest                (Deployment: polls simulators, port-forwarded to :9090)
└── writeback             (Deployment: reads deltas, pushes to simulators, port-forwarded to :9091)
```

#### Bootstrap Sequence

1. **Skaffold builds images** from submodules (in-and-out engine, simulator, pg-trickle, osi-mapping)
2. **Deploy postgres-pgtrickle** StatefulSet — pg_trickle preloaded, `CREATE EXTENSION pg_trickle`
3. **Run migrate Job:**
   - Alembic creates in-and-out schema tables
   - OSI-mapping engine reads `mapping.yaml`, generates materialized view SQL
   - `convert_matviews_to_pgtrickle.py` rewrites to stream table calls
   - `psql` applies → stream tables registered, DAG built
4. **Deploy simulator** — pre-seeded with connector seed data
5. **Deploy ingest** — polls both simulators, writes to PG
6. **pg-trickle processes** — CDC triggers fire, stream tables update incrementally through DAG
7. **Deploy writeback** — reads delta stream tables, pushes merged data to simulators
8. **Verify** — port-forward to simulator UI, check merged data; query `pgtrickle.quick_health`

#### End-to-End Test Scenario

1. Simulators start with seed data (Acme in both, Alice in both, Bob HS-only, Kari TT-only)
2. Ingest runs → Acme merges (org_number), Alice merges (email), Bob and Kari remain single-source
3. Writeback pushes:
   - Bob → Tripletex (new contact insert + `customer.id` set to Acme's TT id)
   - Kari → HubSpot (new contact insert + new association to Acme's HS id)
   - Alice: name from HubSpot (priority 1), address from Tripletex (priority 1) written to both systems
   - Acme: company name from HubSpot, address from Tripletex written to both
4. Mutate Kari's phone in Tripletex simulator → ingest picks up change → phone resolves via last_modified → writeback updates HubSpot
5. Verify convergence: all delta stream tables return 0 rows

## Integration Seams (Critical Gaps)

These are the places where the three repos must agree on contracts. Each is a concrete task.

### Seam 1: in-and-out output schema → OSI-mapping source declarations

in-and-out creates tables like `inandout.hubspot__contacts` (connector name + datatype, double-underscore). OSI-mapping `sources:` currently reference logical names like `hubspot_contacts`. The mapping must use the **actual table names** in-and-out produces, or we add a schema/table override in the mapping's `source:` block.

**Action:** Run in-and-out ingest once, inspect the PG schema, and update the OSI-mapping source dataset names to match.

### Seam 2: OSI-mapping delta views → in-and-out writeback contract

in-and-out writeback reads from `desired_state_{connector}_{datatype}` tables/views. These must contain:
- `_external_id` — the target system's primary key (for existing records)
- `_cluster_id` — the golden record ID (for new records that need to be created)
- Payload columns matching the connector's writeback field expectations

**Action:** Determine the exact desired-state view contract in-and-out expects. Then verify OSI-mapping's reverse views produce compatible output, or add a thin adapter view.

### Seam 3: pg-trickle SQL compatibility

The SQL generated by OSI-mapping must be within pg-trickle's supported operator set. Key concerns:
- Recursive CTEs (for transitive identity resolution) — ✅ supported
- Window functions (for `COALESCE` with priority ordering) — ✅ supported
- LATERAL joins (for embedded/nested sources) — ✅ supported
- `jsonb_*` functions (for nested field access) — ✅ supported as LATERAL SRFs

**Action:** Generate the SQL from mapping.yaml, run through the pg-trickle validator, and fix any unsupported constructs.

## Monitoring & Observability

Both in-and-out and pg-trickle expose rich metrics and health views. This section maps what's available to what we should use in the example.

### in-and-out Metrics (Prometheus)

The engine exposes `/metrics` (Prometheus text format) on its HTTP port (ingest `:9090`, writeback `:9091`).

**Counters:**

| Metric | Labels | Why it matters for this example |
|--------|--------|--------------------------------|
| `inout_records_processed_total` | `connector`, `datatype`, `operation` | Track ingestion throughput per system/entity |
| `inout_http_errors_total` | `connector`, `datatype`, `status_code` | Detect simulator or API failures |
| `inout_conflicts_detected_total` | `connector`, `datatype`, `resolution` | Monitor CAS conflicts during writeback |
| `inout_schema_changes_total` | `connector`, `datatype`, `change_type` | Detect if in-and-out schema drifts from OSI-mapping expectations |

**Gauges:**

| Metric | Labels | Why it matters |
|--------|--------|----------------|
| `inout_sync_lag_seconds` | `connector`, `datatype` | Convergence tracking — should drop to zero after full cycle |
| `inout_circuit_breaker_state` | `connector`, `datatype` | 0=closed (healthy), 0.5=half_open, 1=open — detect persistent failures |
| `inout_dead_letter_depth` | `connector`, `datatype` | Failed records needing manual review |
| `inout_connector_health_score` | `connector`, `datatype` | Composite health 0.0–1.0 |

**Histograms:**

| Metric | Labels | Why it matters |
|--------|--------|----------------|
| `inout_sync_duration_seconds` | `connector`, `datatype`, `operation` | Measure ingest/writeback cycle time |
| `inout_http_request_duration_seconds` | `connector`, `datatype`, `method` | Detect slow API calls to simulators |

**Health endpoints:** `GET /health` (liveness), `GET /ready` (readiness), `GET /metrics`.

### pg-trickle Monitoring (SQL Views & Functions)

pg-trickle exposes monitoring via SQL views and functions — no external exporter needed for the example. A Postgres exporter can scrape these for Prometheus if desired.

**Key views:**

| View / Function | What it tells you |
|-----------------|-------------------|
| `pgtrickle.quick_health` | Single-row dashboard: `total_stream_tables`, `error_tables`, `stale_tables`, `scheduler_running`, overall `status` (OK/WARNING/CRITICAL) |
| `pgtrickle.pg_stat_stream_tables` | Per-ST: status, refresh_mode, staleness, total/successful/failed refreshes, avg_duration_ms, consecutive_errors |
| `pgtrickle.stream_tables_info` | Same as catalog + computed `staleness` interval and `stale` boolean |

**Diagnostic functions:**

| Function | Use case |
|----------|----------|
| `pgtrickle.health_check()` | Multi-check: scheduler_running, error_tables, stale_tables, buffer_growth (>10K rows), slot_lag |
| `pgtrickle.refresh_timeline(N)` | Last N refreshes across all STs — see start_time, rows_inserted/deleted, duration_ms, errors |
| `pgtrickle.st_refresh_stats()` | Per-ST aggregate: total_refreshes, avg_duration_ms, stale flag |
| `pgtrickle.dependency_tree()` | ASCII DAG view of ST→ST→source dependencies — verify the pipeline topology is correct |
| `pgtrickle.change_buffer_sizes()` | Per-source CDC buffer pending_rows + buffer_bytes — detect stalled refreshes |
| `pgtrickle.diagnose_errors(name)` | Last 5 failures for a ST, classified by type with remediation hints |

**For the example, the key convergence check is:**

```sql
-- Are all stream tables healthy and up-to-date?
SELECT * FROM pgtrickle.quick_health;
-- Expected: status = 'OK', stale_tables = 0, error_tables = 0

-- Are any delta views non-empty? (means writeback has pending work)
SELECT stream_table, pending_rows
FROM pgtrickle.change_buffer_sizes()
WHERE pending_rows > 0;
```

### Recommended Observability Stack for the Example

**Minimal (default):** Just check health via CLI after each cycle:
```bash
# in-and-out health
curl http://localhost:9090/health   # ingest
curl http://localhost:9091/health   # writeback

# pg-trickle health
psql -c "SELECT * FROM pgtrickle.quick_health;"
psql -c "SELECT * FROM pgtrickle.refresh_timeline(10);"
```

**Full stack (opt-in):** Add Prometheus + Grafana as K8s manifests in `k8s/overlays/observability/`. in-and-out already ships a `servicemonitor.yaml` in its `k8s/` directory. Adds:
- Prometheus scraping ingest (`:9090/metrics`) and writeback (`:9091/metrics`) via ServiceMonitor
- Grafana dashboards (provisioned via ConfigMap) for sync lag, record throughput, error rates
- postgres_exporter for pg-trickle views (optional)

Enable via a Skaffold profile:
```bash
skaffold dev -p observability
```

pg-trickle views can be scraped into the same Prometheus via [postgres_exporter](https://github.com/prometheus-community/postgres_exporter) custom queries, but this is unnecessary for the example — the SQL views are sufficient.

## Scalability Considerations

This is an example setup. The scale targets are modest: ~10 records per entity, sub-second convergence. But the architecture has known scaling properties worth noting:

| Concern | Impact at small scale | What changes at production scale |
|---------|----------------------|----------------------------------|
| pg-trickle refresh | IMMEDIATE mode: microseconds per DML | For high-throughput ingest, switch to scheduled DIFFERENTIAL mode (e.g. `@every 5s`) to batch CDC |
| Identity resolution | Small cluster tables, fast | WITH RECURSIVE identity chains become expensive >100K records — may need to pre-cluster or use fixed-point IVM |
| Writeback throughput | Single-threaded, fine for 10 records | in-and-out supports concurrent writeback workers. Rate limiting is per-connector. |
| Simulator | In-memory/SQLite, fast | N/A — simulators are dev-only |
| Single Postgres | Plenty for PoC | Production: CNPG cluster, read replicas for BI, connection pooling |

**Not a Phase 1 concern.** Revisit when moving toward real API integration or >1K records.

## Gap Analysis Summary

| # | Gap | Severity | Owner | Notes |
|---|-----|----------|-------|-------|
| G1 | HubSpot association writeback missing in connector | **Blocker** | in-and-out | Fix in `vendor/in-and-out`, push upstream |
| G2 | Tripletex contacts incremental ingestion missing | **Blocker** | in-and-out | Fix in `vendor/in-and-out`, push upstream |
| G3 | Simulator seed data not aligned for merge scenario | High | in-and-out | Fix in `vendor/in-and-out`, push upstream |
| G4 | in-and-out output schema ↔ OSI-mapping source names | **Blocker** | Integration | Run ingest once via `skaffold dev`, inspect PG, update mapping |
| G5 | OSI-mapping delta views ↔ in-and-out writeback contract | **Blocker** | Integration | May require changes in `vendor/osi-mapping` or `vendor/in-and-out` |
| G6 | pg-trickle SQL compatibility validation | High | Integration | Run `pgtrickle.validate_query()` on generated SQL |
| G7 | pg-trickle Dockerfile.hub builds + works in StatefulSet | Medium | pg-trickle | Verify build from `vendor/pg-trickle` |
| G8 | OSI-mapping `--materialized-views` → `convert_matviews_to_pgtrickle.py` pipeline tested | Medium | Integration | Script in migrate Job |
| G9 | Country vocabulary mapping (Tripletex "Norge" ↔ ISO "NO") | Low (defer) | mapping.yaml | Not needed for initial example |
| G10 | Skaffold config + K8s manifests for all services | High | This repo | New: write `skaffold.yaml`, `k8s/` manifests |
| G11 | OSI-mapping engine needs a Dockerfile | Medium | osi-mapping | May need to add in `vendor/osi-mapping` |

## File Inventory

```
sesam-opensource-poc/
├── vendor/
│   ├── in-and-out/                 # git submodule
│   ├── pg-trickle/                 # git submodule
│   └── osi-mapping/                # git submodule
├── skaffold.yaml                   # Build + deploy config
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   ├── postgres.yaml           # StatefulSet: PG 18 + pg_trickle
│   │   ├── simulator.yaml          # Deployment: FastAPI simulators
│   │   ├── migrate-job.yaml        # Job: alembic + osi-mapping + stream tables
│   │   ├── ingest.yaml             # Deployment: in-and-out ingest
│   │   ├── writeback.yaml          # Deployment: in-and-out writeback
│   │   ├── configmap.yaml          # Connector YAMLs
│   │   └── services.yaml           # ClusterIP services
│   └── overlays/
│       └── dev/
│           └── kustomization.yaml  # Dev: port config, resource limits
├── connectors/
│   ├── hubspot.yaml                # Expanded connector
│   └── tripletex.yaml              # Expanded connector
├── mappings/
│   └── mapping.yaml                # OSI-mapping config
├── scripts/
│   └── generate-stream-tables.sh   # osi-mapping → matviews → pg-trickle (used by migrate Job)
└── .gitmodules                     # Submodule definitions
```
