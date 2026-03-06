# API Gateway & Traffic Control (Ingress + Egress)

Define how API gateway/APIM capabilities are used for both inbound webhook traffic and outbound calls to HubSpot/Tripletex.

## Scope

- External ingress endpoint strategy for webhook callbacks
- Outbound API call governance for reads and write-backs
- Auth and request validation at the edge
- Rate limiting, quotas, and traffic shaping
- API call monitoring and alert signals (ingress + egress)
- Rollout approach from PoC to stricter production controls

## Options

### Option A: Managed APIM

- **What:** Use a managed API gateway/APIM product for inbound webhook front door and outbound provider-facing API policies.
- **Pros:**
  - Centralized policy governance and key rotation
  - Strong auth, quota, and analytics capabilities
  - Unified controls across environments and teams
- **Cons:**
  - Platform dependency and additional cost
  - More policy/config surface to manage
- **Effort:** Medium

### Option B: Kubernetes-native ingress stack — Recommended for Phase 1

- **What:** Use NGINX Ingress Controller (or Traefik) + cert-manager for ingress, and enforce outbound throttling/retry/concurrency in application workers.
- **Pros:**
  - Native fit with Skaffold/Kubernetes workflow
  - Lower operational overhead for PoC scale
  - Good local/dev parity and straightforward deployment
- **Cons:**
  - Less centralized governance than enterprise APIM
  - Outbound policy is mostly implemented in app/orchestrator layer
- **Effort:** Low-Medium

### Option C: Envoy Gateway / Istio

- **What:** Use Envoy-based gateway and policy model for ingress, plus service-mesh traffic policy for controlled egress.
- **Pros:**
  - Powerful and flexible policy model
  - Strong observability and traffic controls
- **Cons:**
  - Steeper complexity for a small PoC
- **Effort:** Medium-High

### Option D: Self-hosted API gateway (Kong/Tyk)

- **What:** Deploy gateway in-cluster with plugins/policies for ingress and optional routed egress.
- **Pros:**
  - APIM-like feature set without managed APIM lock-in
  - Good extensibility via plugins
- **Cons:**
  - Added component ownership and upgrade burden
- **Effort:** Medium

## Recommended Path

- **Phase 1 (PoC):** Option B (Kubernetes-native ingress baseline) + outbound throttling/concurrency controls in workers.
- **Phase 2:** Add or migrate to Option A when centralized governance, quota management, and cross-system API analytics become required.

## Baseline Controls (all options)

Ingress:
- Enforce HTTPS and strict TLS config.
- Validate provider auth/signature headers.
- Apply rate limits with burst controls per provider route.
- Set request body size limits and basic schema guards.
- Emit ingress metrics and error rates to Prometheus/Grafana.

Egress:
- Apply per-provider/API-route throttling (requests/sec and concurrency caps).
- Use retry with exponential backoff + jitter and circuit-breaker behavior for persistent failures.
- Enforce idempotency keys for write-back operations where supported.
- Emit outbound call metrics (latency, status codes, throttled calls, retry counts).
- Use queue/batch controls so write-back bursts do not exceed provider limits.

## Egress Deployment Patterns

### Pattern 1: Direct egress with worker-level controls (Phase 1 default)

- Workers call provider APIs directly.
- Throttling and retry logic implemented in ingestion/write-back clients.
- Lowest complexity; sufficient for PoC.

### Pattern 2: Egress via internal gateway/proxy

- Workers route outbound API calls through controlled gateway/proxy path.
- Centralizes outbound quotas, auth rotation, and API call observability.
- Higher complexity, better governance at scale.

## Related Plans

- [Webhooks](../ingest/webhooks.md)
- [Data Ingestion](../ingest/data-ingestion.md)
- [MDM Rules & Write-Back](../sync/mdm-rules-writeback.md)
- [Orchestration](orchestration.md)
- [Monitoring](monitoring.md)
- [Traceability](traceability.md)

## Open Questions

- Do we need centralized API key lifecycle/audit requirements already in Phase 1, or can they wait for Phase 2?
- Are provider source IP ranges stable enough to enforce ingress IP allow-listing?
- Which outbound APIs need centralized egress governance first (HubSpot write-back, Tripletex write-back, or both)?
