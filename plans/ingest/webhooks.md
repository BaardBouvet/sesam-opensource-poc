# Webhooks

Receive and process webhook events from HubSpot and Tripletex for low-latency sync, with polling as reconciliation fallback.

## Scope

- Webhook receiver service (`webhook-receiver`)
- Provider webhook configuration and signature validation
- Event ingestion, deduplication, retry, and replay
- Triggering downstream ingestion/linkage/materialization jobs

## Tripletex Webhook Reference

- Canonical project reference: `../../specs/tripletex/webhook-reference.md`
- Source docs: https://developer.tripletex.no/docs/documentation/webhooks/
- Event catalog endpoint: `GET /v2/event`
- Subscription management endpoints:
  - `GET /v2/event/subscription`
  - `POST /v2/event/subscription`
  - `PUT /v2/event/subscription/{id}`
- Relevant events for this PoC:
  - `contact.create`, `contact.update`, `contact.delete`
  - `customer.create`, `customer.update`, `customer.delete`
- Phase 1 subscription profile:
  - `contact.*` with `fields=*`
  - `customer.*` with `fields=*`

## Tripletex Webhook Field Profile (Phase 1)

- Event coverage: `contact.create/update/delete` and `customer.create/update/delete`.
- Delete events provide `id` with `value=null`, so read-after-webhook remains required for reconciliation/deletion handling.
- Create/update events include `value` (optionally filtered by subscription `fields`), while nightly reconciliation remains enabled.

Rationale:
- Minimizes follow-up reads for create/update handling in PoC phase.
- Keeps mapping logic simple while schemas are still evolving.
- Preserves nightly reconciliation and read-after-delete safety.

Phase 2 optimization path:
- Narrow to curated field sets once payload volume and processing costs are measured.

## Options

### Option A: Dedicated Receiver + Dagster Sensor (Recommended)

- **What:** FastAPI receiver accepts webhooks and writes to `incoming_events`. Dagster sensor consumes queued events and triggers scoped assets.
- **Pros:**
  - Clear separation between network ingress and pipeline execution
  - Fast provider response path (`2xx` after durable enqueue)
  - Easy idempotency + retry handling
  - Works with webhook + polling hybrid
- **Cons:**
  - Extra service and schema to maintain
- **Effort:** Medium

### Option B: Receiver triggers Dagster directly

- **What:** Receiver calls Dagster API/run launcher directly on each webhook.
- **Pros:**
  - Fewer moving pieces
- **Cons:**
  - Risky coupling and burst amplification
  - Harder replay and deduplication controls
- **Effort:** Low-Medium

### Option C: Event Bus First (Kafka/NATS)

- **What:** Receiver publishes events to broker; workers consume and trigger pipelines.
- **Pros:**
  - Best scale and fan-out
- **Cons:**
  - Infra heavy for PoC
- **Effort:** High

### Option D: APIM Gateway Front Door

- **What:** Put a gateway layer in front of webhook receiver endpoints.

## Receiving Path

### Runtime (Skaffold + Kubernetes)

- Expose receiver behind HTTPS Ingress (optionally behind APIM gateway).
- Endpoints:
  - `POST /webhooks/hubspot`
  - `POST /webhooks/tripletex`
- Provider target URLs point to these routes.

If APIM is used, provider `targetUrl` points to APIM public URL, and APIM forwards to internal receiver service.

### Local Development

- Run receiver in devcontainer.
- Expose via secure tunnel (`cloudflared`/`ngrok`) for provider callbacks.
- Keep scheduled polling enabled during local work.

## Security

- HubSpot: validate signature headers (`X-HubSpot-Signature` family).
- Tripletex: configure callback auth in subscription (`authHeaderName` + `authHeaderValue`) and validate it in receiver.
- Enforce HTTPS in all non-local environments.
- Reject stale/replayed timestamps where supported.

Tripletex callback auth options supported by provider docs:
- custom header (recommended)
- basic auth in callback URL
- query/path secret token (least preferred)

## Webhook Registration Strategy

### HubSpot (manual)

- Registration is treated as manual app configuration in Phase 1.
- Keep a runbook/checklist for required subscriptions and callback URL setup.
- Validate registration via sandbox/test event before enabling regular processing.

### Tripletex (API-driven)

- Manage subscriptions via API (`GET/POST/PUT /v2/event/subscription`).
- Add bootstrap job/script to create/update subscriptions idempotently during environment setup.
- Persist expected subscription config and reconcile drift periodically.
- Subscription status monitoring is required (not optional).
- Poll subscription status on a fixed cadence and alert on non-active states.
- If status changes to `DISABLED_TOO_MANY_ERRORS`, trigger remediation runbook and re-enable via `PUT /v2/event/subscription/{id}` after fixing receiver issues.

Simulator requirement:
- Test simulators must support both registration patterns: HubSpot-style manual/pre-seeded registration and Tripletex-style API registration lifecycle.

#### Remediation Checklist (Tripletex)

1. **Detect:** Alert fires for non-active subscription status.
2. **Diagnose:** Check receiver health, auth header validation, recent 4xx/5xx rates, and callback endpoint reachability.
3. **Fix:** Correct root cause (auth mismatch, endpoint outage, rate-limit pressure, payload handling bug).
4. **Re-enable:** Call `PUT /v2/event/subscription/{id}` to re-enable the subscription.
5. **Verify:** Trigger or wait for a test event and confirm it reaches `incoming_events` and downstream processing.
6. **Close-out:** Record incident notes and update runbook thresholds/policies if needed.

## Delivery Semantics

- Assume at-least-once delivery.
- Assume out-of-order delivery.
- Deduplicate with key: `source + event_id + object_id + occurred_at` (or best available equivalent).
- Persist raw payload + normalized event row.

Precedence policy with full sync:
- Webhooks are treated as low-latency signals, not final authority.
- Apply webhook updates immediately as `provisional` state.
- Full sync reconciliation is canonical and can overwrite provisional state.
- Resolve conflicts by `(source_version, updated_at, ingest_time)`.

Tripletex-specific delivery notes:
- Provider retries failed deliveries with increasing delay over ~30 hours.
- Subscription may be set to `DISABLED_TOO_MANY_ERRORS` after repeated failures; re-enable via `PUT /v2/event/subscription/{id}` after remediation.
- Provider recommends periodic full sync (e.g., nightly) because rapid updates may skip intermediate versions while still sending latest.

Operational requirement:
- Treat Tripletex subscription status as an SLO signal; non-active subscription state is a production incident for webhook freshness.

## Data Model

### `incoming_events`

- `id` (pk)
- `source_system` (`hubspot` | `tripletex`)
- `event_type`
- `event_id`
- `object_type`
- `object_id`
- `occurred_at`
- `payload_json`
- `dedupe_hash` (unique)
- `received_at`
- `status` (`new`, `processed`, `failed`, `dead_letter`)
- `attempt_count`
- `last_error`
- `provider_subscription_id`

### `failed_events`

- `incoming_event_id`
- `failed_at`
- `error_type`
- `error_message`
- `retry_after`

## Retry & Replay

- Exponential backoff retries for transient failures.
- Dead-letter after max attempts.
- Replay command by time range/source/object type from `incoming_events`.
- Keep retention window (e.g. 30 days for PoC) for reproducibility.

Tripletex payload contract:
- Callback body fields: `subscriptionId`, `event`, `id`, `value`
- For `*.delete`, `value` is `null` and only `id` is guaranteed.
- For create/update, `value` may be filtered by subscription `fields`.

## Phase 2 Advanced Features

### Option A: Replay + backfill tooling (Recommended)

- Reprocess events by source/object/time window from `incoming_events`.
- Required for incident recovery and validation.

### Option B: Event bus fan-out

- Publish normalized events to Kafka/NATS for parallel consumers.
- Best for larger volume and multiple downstream subscribers.

### Option C: Adaptive batching/coalescing

- Coalesce burst updates per object and trigger fewer downstream runs.
- Reduces cost and run-churn under high event rates.

Implementation notes:
- Keep core receiver idempotency and dedupe unchanged.
- Add replay CLI/job with dry-run mode.
- Add coalescing window per object type (e.g., 15-60s).

## Related Plans

- [Data Ingestion](data-ingestion.md)
- [Deletion Tracking & Verification](deletion-tracking.md)
- [Orchestration](../ops/orchestration.md)
- [API Gateway & Traffic Control](../ops/api-gateway-traffic-control.md)
- [Traceability](../ops/traceability.md)
- [Monitoring](../ops/monitoring.md)

## Simulator Parity Checklist

- Webhook registration flows are supported for both provider patterns (HubSpot manual/pre-seeded, Tripletex API-driven).
- Subscription lifecycle states are testable (`active`, disabled, error states including `DISABLED_TOO_MANY_ERRORS` behavior).
- Callback payloads match provider-specific shape and required fields.
- Delivery characteristics are testable: duplicate events, out-of-order events, and retries.
- Registration/status drift scenarios are testable (missing subscription, wrong callback URL/auth, disabled subscription).
- Recovery flow is testable end-to-end (fix + re-enable + verify event reaches `incoming_events`).

## Open Questions

- At what measured payload size/throughput should we narrow from `fields=*` to curated field subsets?
- At what event volume do we introduce Kafka/NATS?
- Should replay update existing audit records or write replay-specific audit entries?
