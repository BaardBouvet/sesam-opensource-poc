# Entity Lineage Trace

Follow a specific entity's journey end-to-end: from a source ingest event in HubSpot or Tripletex, through the golden record, to a writeback event in any target — and trace backward from a target write to the source events that caused it.

## Context

The pipeline has two observable edges:

- **Inbound edge**: events arriving from HubSpot/Tripletex land in `inout_src_{connector}_{datatype}` staging tables under a `_sync_run_id`.
- **Outbound edge**: writeback decisions are recorded in `inout_ops_writeback_result` with `field_diff` and `payload_snapshot`.

Between those edges, identity resolution (OSI-Mapping) assigns golden IDs (`cluster_id`), and IVM materialises golden record views. An operator should be able to answer:

- **Forward**: "HubSpot company `123` changed at 14:32 — what did the pipeline do with it? Did it update the golden record? Which Tripletex customer was written as a result?"
- **Backward**: "We wrote to Tripletex customer `456` at 15:01 — why? Which source event caused it? What fields came from where?"

This is **entity-level lineage**. It is distinct from — and complementary to — the run-level observability already in place (`inout_ops_sync_run`, Prometheus metrics, structured logs).

## What Already Exists

The building blocks for entity lineage are almost entirely present:

| Building Block | Table / Component | What It Provides |
|---|---|---|
| Source change history | `inout_src_{connector}_{datatype}` + `_history` (append mode) | When an entity changed and via which sync run |
| Run context | `inout_ops_sync_run` | Start/end, mode, high-water marks per run |
| Webhook events | `inout_ops_webhook_log` | Raw webhook receipts keyed by `connector, datatype, external_id` |
| Source provenance | `_lineage JSONB` column on source tables | Stores `run_id`, `api_path`, `page_number` per row |
| Identity bridge | `inout_ops_identity_map` (migration 012+021) | Maps `cluster_id` ↔ `(connector, datatype, target_external_id)` — the golden-to-target join |
| Write audit | `inout_ops_writeback_result` | `action`, `field_diff`, `payload_snapshot`, `run_id`, `processed_at` per target write |
| OTel SDK | `opentelemetry-sdk` + OTLP exporter | Configured, disabled by default; spans created around sync/writeback cycles |
| Structlog | `run_id` bound per cycle | All log lines carry `run_id`, `connector`, `datatype` |

The identity map's `cluster_id` column is the critical pivot. Given `cluster_id`, you can look up all source IDs (from OSI-Mapping's `entity_cluster_member`) and all target IDs (from `inout_ops_identity_map`) and therefore all writeback results.

## Gap Analysis

These four things are missing to make entity lineage queryable:

1. **No `cluster_id` on writeback results.** `inout_ops_writeback_result` knows the `external_id` in the target system but not the MDM `cluster_id`. Tracing backward requires a reverse lookup through `inout_ops_identity_map`, which works but is an extra hop and requires knowing the connector/datatype.

2. **No direct source → golden join within in-and-out.** The source entity's `external_id` maps to a `cluster_id` via OSI-Mapping's `entity_cluster_member`, which lives outside in-and-out. Forward tracing from source requires querying that table (or its equivalent in the OSI-Mapping schema).

3. **Dynamic table names block generic SQL.** Source tables are named `inout_src_hubspot_companies` — there is no static view spanning all source tables. Generic trace queries require dynamic SQL or a registering layer.

4. **No trace visualization layer.** The data lives in Postgres; there is no UI, CLI command, or API endpoint that shows the chain in human-readable form.

## Existing Tools Survey

### OpenLineage / Marquez

OpenLineage is an open standard for data pipeline lineage. Marquez is its reference catalog/UI. Tracks **job → dataset → job** dependencies (e.g., "dbt model `stg_companies` reads `inout_src_hubspot_companies`"). Granularity is dataset (table), not entity (row). HubSpot company `123` is invisible — Marquez knows the `companies` table was read, not which row changed or why. **Not a fit for entity-level trace.**

### DataHub / Amundsen / Apache Atlas

Enterprise data catalogues with lineage graphs at the schema/table level. Heavier than Marquez, same fundamental limitation: no row-level entity causality. Atlas adds governance/classification metadata, but neither it nor DataHub understands "entity `123` caused entity `456`". **Not a fit.**

### Jaeger / Grafana Tempo

Distributed tracing backends with rich span-waterfall UIs. Applicable if each entity's lifecycle is mapped to an OTel trace:

- Root span: *entity ingested* (`connector=hubspot, datatype=companies, external_id=123`)
- Child span: *golden record updated* (`cluster_id=G1`)
- Child span(s): *writeback sent* (`connector=tripletex, datatype=customers, external_id=456`)

The OTel SDK is already present and configured. The **challenge** is trace context propagation: in-and-out's pipeline stages are separated by minutes or hours (ingest → next MDM cycle → writeback). OTel traces are designed for millisecond-bounded request flows. Propagating a `traceparent` through Postgres rows (rather than HTTP headers) is non-standard. Traces spanning hours exist technically — Jaeger renders them — but they are unusual and the span-timeline view becomes less useful with multi-hour gaps. Retention also needs to be extended beyond typical defaults.

This approach is compelling for visualization once the causal data is available, but not as the primary storage mechanism.

### Temporal

Durable workflow execution with a full history UI. Per-entity tracing is natural if every entity is modelled as a workflow instance (`workflowId = "hubspot_company_123"`). Temporal gives a complete activity history with inputs, outputs, and timing for free. **Too big a commitment**: requires rewriting the pipeline as Temporal workflows and running the Temporal server. Overkill for a 2-source PoC.

### Dagster

Asset-based orchestrator with run history and asset lineage UI. Dagster's asset partitions can represent entity-grain lineage if each entity ID is a partition. In practice, partitioning Dagster assets by entity ID (potentially thousands of IDs, high churn) is unconventional and not well-optimised. Dagster's lineage is at the asset (table/view) level, not the row level. Could provide model-level lineage (ingest asset → golden asset → writeback asset) but not "company 123 specifically". **Useful complement but not a replacement.**

### Sesam (SaaS MDM, original product)

Sesam's pipe model is the closest existing system to what we want: every entity change is event-sourced, the `_origin` claim tracks which source pipe each field came from, and the UI shows the "trace" of an entity through the pipe graph. There is no open-source equivalent. This is the design inspiration.

### Summary

No existing open-source tool provides entity-level MDM lineage out of the box. The infrastructure must be built, but almost all the data is already present in Postgres.

## Options

### Option A: SQL Trace Functions (Query-Time Joins)

Add two parameterised SQL functions that reconstruct the lineage chain at query time by joining across existing tables. No new tables; zero write overhead.

**Forward trace**: `entity_forward_trace(p_connector TEXT, p_datatype TEXT, p_external_id TEXT)`

```sql
-- Returns: ingestion events → identity map → writeback results for a source entity
SELECT
  h.external_id                    AS source_id,
  h._ingested_at                   AS ingested_at,
  h._sync_run_id                   AS sync_run_id,
  sr.mode, sr.status,
  h._lineage                       AS source_lineage,
  im.cluster_id,
  im.connector                     AS target_connector,
  im.datatype                      AS target_datatype,
  im.target_external_id,
  wr.action, wr.processed_at,
  wr.field_diff
FROM inout_src_hubspot_companies h          -- dynamic: connector+datatype determines table
JOIN inout_ops_sync_run sr
  ON sr.id = h._sync_run_id
JOIN entity_cluster_member ecm              -- OSI-Mapping table
  ON ecm.source_system = 'hubspot'
  AND ecm.source_id = h.external_id
JOIN inout_ops_identity_map im
  ON im.cluster_id = ecm.golden_id
LEFT JOIN inout_ops_writeback_result wr
  ON wr.connector = im.connector
  AND wr.datatype = im.datatype
  AND wr.external_id = im.target_external_id
WHERE h.external_id = :external_id
ORDER BY h._ingested_at;
```

The source table name must be constructed dynamically (PL/pgSQL `EXECUTE` or application-side substitution).

- **Pros**: No schema changes; no write overhead; uses only existing tables; immediate availability.
- **Cons**: Requires dynamic SQL for the source table name; multi-hop joins are slow on large datasets without covering indexes; no causal link between ingest timing and writeback timing (the functions return correlated rows, not a timestamped causal chain); difficult to extend to multi-hop chains (source A → golden → source B via reingest signal).

### Option B: Entity Event Log Table

Add a single append-only table `inout_ops_entity_event` written by the ingestion and writeback engines. Each row records one observable event on one entity. A `caused_by_event_id` self-reference forms a causal DAG.

```sql
CREATE TABLE inout_ops_entity_event (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_kind          TEXT        NOT NULL,  -- ingested | writeback_sent | writeback_failed
    connector           TEXT        NOT NULL,
    datatype            TEXT        NOT NULL,
    external_id         TEXT        NOT NULL,
    cluster_id          TEXT,                  -- NULL until identity resolved
    sync_run_id         UUID        REFERENCES inout_ops_sync_run(id),
    writeback_result_id UUID,                  -- FK inout_ops_writeback_result (future)
    field_diff          JSONB,
    caused_by_event_id  UUID        REFERENCES inout_ops_entity_event(id),
    trace_id            TEXT        -- OTel W3C trace-id, optional
);

CREATE INDEX ON inout_ops_entity_event (connector, datatype, external_id, occurred_at DESC);
CREATE INDEX ON inout_ops_entity_event (cluster_id, occurred_at DESC);
CREATE INDEX ON inout_ops_entity_event (caused_by_event_id);
```

**Forward trace**: `WHERE connector='hubspot' AND datatype='companies' AND external_id='123' ORDER BY occurred_at` — returns all events on the source entity; follow `caused_by_event_id` forward to find the `writeback_sent` events.

**Backward trace**: `WHERE connector='hubspot' AND datatype='companies' AND external_id='456' AND event_kind='writeback_sent'` → follow `caused_by_event_id` up the chain to the originating `ingested` event.

The `caused_by_event_id` is populated:
- In the writeback engine: when writing `writeback_sent`, set `caused_by_event_id` to the most recent `ingested` event for the same `cluster_id` (lookable via `inout_ops_identity_map` → source `external_id` → most recent entity event row). This is a best-effort causal attribution: "the last known ingest event for this golden entity was the proximate cause".
- In the ingestion engine: `ingested` events have `caused_by_event_id = NULL` (they are root causes) unless they are triggered by a `REINGEST_SIGNAL` from writeback, in which case the signalling writeback event is the cause.

**What about `golden_updated` events?** The IVM runs inside PostgreSQL (pg-trickle), not in Python — it cannot write directly to this table. Two approaches:
- **Skip it for Phase 1**: omit `golden_updated` kind entirely. The chain is still readable: the time gap between an `ingested` event and the subsequent `writeback_sent` event with the same `cluster_id` implicitly contains the IVM cycle.
- **Post-cycle emission**: the orchestrator (Dagster) emits a `golden_updated` event after each MDM cycle by diffing the golden record materialised view against the previous state. Adds one emit per entity per affected cycle.

- **Pros**: Clean, append-only, self-documenting; forward and backward trace from the same table; `cluster_id` makes the golden pivot explicit; works across all dynamic source tables; `trace_id` column ready for OTel correlation; survives process restarts (unlike in-memory event bus).
- **Cons**: Two additional write calls per entity event (low overhead but non-zero); best-effort causal attribution (the `caused_by_event_id` is the latest prior event, not necessarily the logically correct one if multiple sources contribute to a golden record simultaneously).

### Option C: OTel Trace per Entity Lifecycle (Jaeger/Tempo Visualisation)

Assign an OTel trace to each entity's modification lifecycle. Propagate the trace context through Postgres state so the writeback engine can resume the same trace as a child span.

**Trace context propagation pattern:**
1. Ingestion creates a new OTel trace; the `traceparent` (W3C format) is serialised and stored in the source table's `_lineage` column: `{"run_id": "...", "traceparent": "00-4bf92f..."}`.
2. The writeback engine reads `_lineage.traceparent` for the cluster's source records, extracts the trace context, and creates child spans under it.
3. The OTel trace now contains: `entity_ingested` (parent) → `golden_record_updated` (child, emitted by orchestrator) → `writeback_sent` (child, per target system).
4. Jaeger/Tempo renders the span waterfall with timing for each stage.

The `trace_id` from the entity event table (Option B) would point into Jaeger for the full span timeline.

- **Pros**: Jaeger/Tempo give a beautiful, no-code-required trace visualisation; OTel SDK already present; integrates with the observability stack already planned.
- **Cons**: Trace context propagation through Postgres rows is non-standard; multi-hour gaps between spans makes Jaeger waterfall views awkward (spans appear collapsed); OTel trace retention must be set to days/weeks rather than the default hours; the trace-context column adds complexity to the ingestion upsert; spans with no explicit parent still need a mechanism to find their parent (the `traceparent` could be stale if an entity is re-ingested multiple times before a writeback occurs and each creates a new root trace).

## Recommendation

**Combine Option B (Phase 1) with Option C (Phase 2) as an optional add-on.**

### Phase 1 — Entity Event Log

1. **Add `inout_ops_entity_event` table** via a new Alembic migration. Start with only two event kinds: `ingested` and `writeback_sent`. Skip `golden_updated` until the orchestrator layer is in place.

2. **Write `ingested` events** in the ingestion engine immediately after a successful upsert. The ingestion engine already has `run_id`, `connector`, `datatype`, `external_id` in scope. Store the `cluster_id` as NULL initially; if the identity map already has a row for this entity at write time, populate it eagerly.

3. **Write `writeback_sent` / `writeback_failed` events** in the writeback engine immediately after `inout_ops_writeback_result` is flushed. Set `cluster_id` from the desired-state row (the writeback engine knows the `cluster_id` it is processing). Set `caused_by_event_id` to the most recent `ingested` event for the same `cluster_id` via a `SELECT max(occurred_at) ... WHERE cluster_id = :cluster_id AND event_kind = 'ingested'`.

4. **Add two SQL views** for operator convenience:

   ```sql
   -- Forward trace for a source entity
   CREATE VIEW entity_forward_trace AS
   SELECT e.*, causal.connector AS caused_connector, causal.external_id AS caused_id
   FROM inout_ops_entity_event e
   LEFT JOIN inout_ops_entity_event causal
     ON causal.caused_by_event_id = e.id;

   -- Backward trace from a writeback event
   CREATE VIEW entity_backward_trace AS
   SELECT wb.*, src.*
   FROM inout_ops_entity_event wb
   LEFT JOIN inout_ops_entity_event src
     ON src.id = wb.caused_by_event_id
   WHERE wb.event_kind IN ('writeback_sent', 'writeback_failed');
   ```

5. **Add a CLI command** `inandout entity-trace --connector hubspot --datatype companies --id 123 [--direction forward|backward]` that queries the event log and renders the chain as a timestamped table. This is the primary operator interface for Phase 1 — no UI required.

6. **Add `cluster_id` to `inout_ops_writeback_result`** as a new column (migration). This makes the writeback audit table self-navigating: given a writeback result, you can immediately find the golden entity and its source events without going through the event log.

### Phase 2 — Optional Visualisation

The table structure from Phase 1 is already Jaeger-ready (the `trace_id` column). Phase 2 steps:

7. **Enable OTel tracing** (`observability.tracing.enabled: true` in connector config). Store the OTel `trace_id` in `inout_ops_entity_event.trace_id` so operators can jump from a SQL trace to Jaeger in one click.

8. **Grafana Postgres datasource panel**: a variable-driven dashboard where the operator selects `connector`, `datatype`, `external_id` and sees the event chain rendered as a table or node graph using Grafana's built-in Postgres datasource. No custom frontend needed. The Grafana stack is already planned (see [observability-setup.md](observability-setup.md)).

9. **REST endpoint** `GET /api/v1/trace/{connector}/{datatype}/{external_id}?direction=forward` returning the chain as JSON. The API layer already exists in `src/inandout/api/`; this is a straightforward query on the event log.

## Schema Summary

```
inout_ops_entity_event
  id                  UUID PK
  occurred_at         TIMESTAMPTZ
  event_kind          TEXT  ingested | writeback_sent | writeback_failed
  connector           TEXT
  datatype            TEXT
  external_id         TEXT  (source or target depending on event_kind)
  cluster_id          TEXT  (MDM golden ID; nullable until resolved)
  sync_run_id         UUID → inout_ops_sync_run.id
  writeback_result_id UUID → inout_ops_writeback_result.id
  field_diff          JSONB
  caused_by_event_id  UUID → inout_ops_entity_event.id  (DAG)
  trace_id            TEXT  (W3C trace-id; optional Phase 2)

inout_ops_writeback_result (addition)
  cluster_id          TEXT  (new column via migration)
```

The causal DAG can be traversed with a recursive CTE:

```sql
WITH RECURSIVE chain AS (
  SELECT * FROM inout_ops_entity_event
  WHERE connector = 'hubspot' AND datatype = 'companies' AND external_id = '123'
  UNION ALL
  SELECT e.* FROM inout_ops_entity_event e
  JOIN chain c ON c.id = e.caused_by_event_id
)
SELECT * FROM chain ORDER BY occurred_at;
```

## Retention

Add to `housekeeping.retention` in the connector config (default 90 days, same as `sync_run_log`):

```yaml
housekeeping:
  retention:
    entity_event_log: "90d"
```

## Open Questions

- Should the `ingested` event be written for every upsert (including no-change rows based on `_raw_hash` equality), or only when the payload actually changed? Writing only on change reduces volume but loses the "I checked and it was the same" signal.
- When multiple source systems contribute to the same cluster (HubSpot + Tripletex both map to company G1), a writeback event has multiple proximate `ingested` causes. Should `caused_by_event_id` be a single FK (latest cause) or an array of UUIDs?
- Who emits `golden_updated` events — the Dagster orchestrator, a pg-trickle trigger, or a post-IVM reconciliation job?

## Related Plans

- [Traceability](traceability.md) — write-level audit trail (complements entity lineage)
- [Observability Setup](observability-setup.md) — Prometheus/Grafana/Jaeger stack
- [Orchestration](orchestration.md) — Dagster asset dependency graph
- [Common Data Model](../model/common-data-model.md) — `cluster_id` / `entity_cluster_member` definitions
- [MDM Rules & Write-Back](../sync/mdm-rules-writeback.md) — `sync_queue` and desired-state views
