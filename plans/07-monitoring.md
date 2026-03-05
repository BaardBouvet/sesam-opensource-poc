# Plan 07: Monitoring & Observability

Monitor when data was read/written, how many changes happened, and surface failures.

## Options

### Option A: Prometheus + Grafana — Recommended

- **What:** Expose metrics via Prometheus, visualize with Grafana dashboards.
- **Metrics:**
  - `ingestion_records_total{source, entity}` — records read per run
  - `writeback_records_total{target, entity, operation}` — records written (insert/update)
  - `writeback_errors_total{target, entity, error_type}` — failed writes
  - `pipeline_duration_seconds{pipeline}` — run duration
  - `golden_records_total{entity}` — current golden record count
  - `linkage_match_rate{entity}` — % of records matched across systems
- **Pros:**
  - Industry standard, massive ecosystem
  - Grafana provides alerting, dashboards, and annotation support
  - Both run easily in containers (Skaffold)
  - Can share Grafana with Tempo/Loki for unified observability
- **Cons:**
  - Extra infrastructure (Prometheus + Grafana containers)
  - Need to instrument code with prometheus_client
- **Effort:** Medium

### Option B: Dagster Built-in Observability

- **What:** Rely on Dagster's built-in asset observations, run status tracking, and Dagster UI.
- **Pros:**
  - Zero extra infrastructure if already using Dagster
  - Asset materialization events naturally track "when" and "how many"
  - Built-in failure tracking and alerting (Slack, email)
- **Cons:**
  - Limited to pipeline-level metrics (not custom business metrics)
  - Dashboard customization is limited
  - Dagster UI is pipeline-focused, not business-focused
- **Effort:** Low

### Option C: Custom Dashboard (Streamlit/Dash)

- **What:** Build a simple web dashboard that queries the audit table and Postgres directly.
- **Pros:**
  - Fully customized to our use case
  - No extra monitoring infrastructure
  - Can show MDM rule decisions, merge stats, etc.
- **Cons:**
  - Another app to build and maintain
  - Reinventing the wheel for basic metrics
- **Effort:** Medium-High

## Recommendation

Use **Option A + B**: Dagster for pipeline observability (runs, failures, asset status) and Prometheus + Grafana for business metrics (record counts, change rates, error rates). Grafana can also host Tempo (traces) and Loki (logs) for a unified observability stack.

## Open Questions

- Do we need alerting in Phase 1? If so, where? (Slack, email, PagerDuty?)
- Should Grafana dashboards be committed as code (JSON/YAML provisioning)?
