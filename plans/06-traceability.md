# Plan 06: Traceability & Audit

Trace every write to HubSpot and Tripletex so we can explain why a change happened and replay/debug API calls.

## Options

### Option A: OpenTelemetry Tracing — Recommended

- **What:** Instrument all API calls with OpenTelemetry spans. Export to Jaeger or Tempo for trace visualization.
- **Pros:**
  - Industry standard, vendor-neutral
  - Rich ecosystem: auto-instrumentation for httpx/requests
  - Correlate reads, MDM decisions, and writes in a single trace
  - Dagster has OpenTelemetry integration
- **Cons:**
  - Needs a trace collector (Jaeger/Tempo) — more infra
  - Trace storage can grow large
- **Components:**
  - `opentelemetry-sdk` + `opentelemetry-instrumentation-httpx`
  - Jaeger (all-in-one container) or Grafana Tempo as backend
  - Link trace IDs to audit log entries in Postgres
- **Effort:** Medium

### Option B: Structured Logging with Correlation IDs

- **What:** Log every API call with structured JSON logs including correlation IDs, request/response bodies, and timestamps.
- **Pros:**
  - Simple, no extra infrastructure (logs go to stdout, Loki, or files)
  - Easy to grep and search
  - Can use Python `structlog` or `loguru`
- **Cons:**
  - No visual trace timeline
  - Harder to correlate across pipeline stages
  - Log volume can be high with full request/response bodies
- **Effort:** Low

### Option C: Audit Table in Postgres

- **What:** Write an audit record to a Postgres `audit_log` table for every write API call.
- **Columns:** `timestamp, trace_id, source_system, entity_type, entity_id, operation, request_body, response_status, response_body, mdm_rule_applied`
- **Pros:**
  - Queryable with SQL — easy to answer "why did this field change?"
  - No extra infrastructure
  - Can be combined with Option A (trace_id links OTel trace to audit row)
- **Cons:**
  - Table grows fast — needs retention policy
  - Doesn't capture read operations naturally
- **Effort:** Low

## Recommendation

Combine **Option A + C**: OpenTelemetry for end-to-end trace correlation, plus a Postgres audit table for queryable write history. The audit table stores the `trace_id` so you can jump from a SQL query to the full trace in Jaeger.

## Open Questions

- Do we need to capture full request/response bodies, or just summaries?
- What retention period for audit data? (7 days? 30 days? indefinite for PoC?)
- Should read operations also be audited, or only writes?
