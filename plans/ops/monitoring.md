# Monitoring & Observability

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
  - `tripletex_webhook_subscription_status{subscription_id,status}` — current Tripletex subscription status
  - `tripletex_webhook_subscription_non_active_total{status}` — count of non-active status transitions
  - `tripletex_webhook_delivery_gap_seconds` — time since last Tripletex webhook received
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

## Required Alerts

- Alert immediately when any Tripletex webhook subscription enters non-active/broken state (including `DISABLED_TOO_MANY_ERRORS`).
- Alert when Tripletex webhook delivery gap exceeds threshold (e.g., no events for expected-active period).
- Alert on repeated subscription re-enable attempts without recovery.

## Tripletex Webhook Incident Runbook

1. Confirm alert source (`tripletex_webhook_subscription_status` or delivery-gap alert).
2. Verify webhook receiver health and callback reachability.
3. Check auth header validation failures and recent 4xx/5xx spikes.
4. Fix root cause and re-enable subscription via `PUT /v2/event/subscription/{id}`.
5. Verify a follow-up event reaches ingestion and processing.
6. Close incident and update alert/runbook thresholds if needed.

## Open Questions

- Which Phase 1 alert channels do we use first? (Slack, email, PagerDuty?)
- Should Grafana dashboards be committed as code (JSON/YAML provisioning)?
