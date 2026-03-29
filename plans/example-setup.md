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

#### Decision: Docker Compose for the Example

ADR-003 specifies Skaffold + K8s for production deployment. For **this example** the question is what serves as the simplest "just run it" experience:

| Option | Pros | Cons |
|--------|------|------|
| **Docker Compose** | Zero K8s knowledge needed. in-and-out already ships a working `docker-compose.yml`. Single `docker compose up`. | Not production-representative. No K8s-native features (HPA, CNPG). |
| **Skaffold + K8s** | Matches ADR-003. Tests production topology. CNPG for PG18 + pg_trickle. | Requires local K8s cluster (minikube/kind). Higher barrier to entry for first run. |
| **Both** | Compose for "try it", Skaffold for "deploy it". | Two configs to maintain. |

**Recommendation:** Start with **Docker Compose** for the example. It's what in-and-out already uses and it gets people to a working demo fastest. Add Skaffold manifests in a follow-up if we want to demonstrate the production path.

#### Runtime Architecture

```
docker compose up
├── postgres           (PG 18 + pg_trickle extension, port 5432)
├── simulator          (FastAPI — both HubSpot + Tripletex endpoints, port 6100)
│   ├── /hubspot/*
│   └── /tripletex/*
├── migrate            (runs once: alembic + OSI-mapping SQL + stream table setup)
├── ingest             (polls both simulators, writes to PG)
└── writeback          (reads delta stream tables, pushes to simulators)
```

The simulator already supports multiple connectors on a single port (path-routed). PG needs the pg_trickle Docker image (`ghcr.io/grove/pg_trickle-ext`) or a custom `Dockerfile` that builds from `postgres:18` + extension install.

#### Bootstrap Sequence

1. **Start PostgreSQL** with pg_trickle preloaded (`shared_preload_libraries = 'pg_trickle'`)
2. **Migrate** — Alembic creates in-and-out's schema tables
3. **OSI-mapping setup** — Run the engine to generate materialized view SQL, pipe through `convert_matviews_to_pgtrickle.py`, apply to PG → stream tables created
4. **Start simulators** — pre-seeded with test data
5. **Start in-and-out ingest** — pulls contacts, companies, associations from both simulators
6. **pg-trickle processes** — CDC triggers fire, stream tables update incrementally through the DAG
7. **Start in-and-out writeback** — reads delta stream tables, pushes merged data back to simulators
8. **Verify** — simulator UIs show merged data; delta stream tables are empty (converged)

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

**Full stack (opt-in):** in-and-out ships `docker-compose.observability.yml` overlay with Prometheus + Grafana + Alertmanager. Adds:
- Prometheus scraping ingest (`:9090/metrics`) and writeback (`:9091/metrics`) at 15s intervals
- Grafana dashboards (pre-provisioned) for sync lag, record throughput, error rates
- Alertmanager for circuit breaker opens, SLA violations

Layer it on with:
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up
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

| # | Gap | Severity | Owner |
|---|-----|----------|-------|
| G1 | HubSpot association writeback missing in connector | **Blocker** | in-and-out |
| G2 | Tripletex contacts incremental ingestion missing | **Blocker** | in-and-out |
| G3 | Simulator seed data not aligned for merge scenario | High | in-and-out |
| G4 | in-and-out output schema ↔ OSI-mapping source names | **Blocker** | Integration |
| G5 | OSI-mapping delta views ↔ in-and-out writeback contract | **Blocker** | Integration |
| G6 | pg-trickle SQL compatibility validation | High | Integration |
| G7 | PG 18 + pg_trickle Docker image for compose | Medium | pg-trickle / this repo |
| G8 | OSI-mapping `--materialized-views` → `convert_matviews_to_pgtrickle.py` pipeline tested | Medium | Integration |
| G9 | Country vocabulary mapping (Tripletex "Norge" ↔ ISO "NO") | Low (defer) | mapping.yaml |

## File Inventory

When complete, the example directory should contain:

```
examples/hubspot-tripletex-sync/
├── docker-compose.yml              # All services
├── connectors/
│   ├── hubspot.yaml                # Expanded from in-and-out example
│   └── tripletex.yaml              # Expanded from in-and-out example
├── mapping.yaml                    # OSI-mapping config (symlink or copy)
├── scripts/
│   └── generate-stream-tables.sh   # osi-mapping → matviews → pg-trickle
├── Dockerfile.postgres             # PG 18 + pg_trickle extension
└── README.md                       # How to run, what to expect
```
