# Observability Setup for the Example

Status: Draft
Date: 2026-03-31

## Context

The example-setup runs ingest, writeback, simulators, OSI-mapping views, and pg-trickle IVM in a single Skaffold-managed kind cluster. The vendor/in-and-out repo already ships a Prometheus + Grafana + Alertmanager stack (via `docker-compose.observability.yml` and `vendor/in-and-out/observability/`) but it is **not yet wired into the K8s/Skaffold deployment**. This plan covers what is needed to bring that stack up in the example cluster.

See [monitoring.md](monitoring.md) for metric definitions, alert rules, and the open question on alert channels.

## What Exists Already

| Component | Location | Status |
|-----------|----------|--------|
| Prometheus config | `vendor/in-and-out/observability/prometheus.yml` | Scrapes `ingest:9090` and `writeback:9091` |
| Alert rules | `vendor/in-and-out/observability/prometheus/alerts.yml` | SLA, circuit-breaker, dead-letter, health alerts |
| Grafana provisioning | `vendor/in-and-out/observability/grafana/provisioning/` | Datasources + dashboard provisioning dirs |
| Grafana dashboards | `vendor/in-and-out/observability/grafana/dashboards/` | Committed dashboard JSON |
| Alertmanager config | `vendor/in-and-out/observability/alertmanager.yml` | Webhook placeholder; Slack/email commented out |
| Docker Compose wiring | `vendor/in-and-out/docker-compose.observability.yml` | Works for local docker-compose; not in K8s |

The in-and-out engine already exposes `/metrics` on its ingest (port 9090) and writeback (port 9091) services. The K8s Services for those pods exist in `k8s/base/ingest.yaml` and `k8s/base/writeback.yaml`.

## What Is Missing

1. **K8s manifests** — Prometheus, Grafana, and Alertmanager Deployments + Services in `k8s/base/`.
2. **ConfigMaps** — mount the existing `observability/` config files into the K8s pods.
3. **kustomization.yaml** — reference the new manifests so they are included in the Skaffold apply.
4. **Skaffold port-forwards** — expose Grafana (3000) and Prometheus (9090) to localhost inside the devcontainer.
5. **Prometheus scrape targets** — use K8s DNS names (`inandout-ingest.sesam-poc.svc`, `inandout-writeback.sesam-poc.svc`) rather than bare `ingest`/`writeback` hostnames from docker-compose.

## Plan

### Step 1 — K8s manifests (`k8s/base/observability.yaml`)

Add a single file with three resources (Prometheus, Grafana, Alertmanager). Keep them minimal:

- **Prometheus**: `Deployment` + `Service` (port 9090). Mount `prometheus.yml`, `prometheus/alerts.yml` via ConfigMap. No persistent volume for the PoC — data lives in the pod.
- **Grafana**: `Deployment` + `Service` (port 3000). Mount `grafana/provisioning/` and `grafana/dashboards/` via ConfigMap. `GF_AUTH_ANONYMOUS_ENABLED=true` / `GF_AUTH_ANONYMOUS_ORG_ROLE=Admin` for zero-friction local access.
- **Alertmanager**: `Deployment` + `Service` (port 9093). Mount `alertmanager.yml` via ConfigMap.

### Step 2 — ConfigMaps via kustomize `configMapGenerator`

Add entries to `k8s/base/kustomization.yaml`:

```yaml
configMapGenerator:
  - name: prometheus-config
    namespace: sesam-poc
    options:
      disableNameSuffixHash: true
    files:
      - prometheus.yml=../../vendor/in-and-out/observability/prometheus.yml
      - alerts.yml=../../vendor/in-and-out/observability/prometheus/alerts.yml

  - name: alertmanager-config
    namespace: sesam-poc
    options:
      disableNameSuffixHash: true
    files:
      - alertmanager.yml=../../vendor/in-and-out/observability/alertmanager.yml

  - name: grafana-dashboards
    namespace: sesam-poc
    options:
      disableNameSuffixHash: true
    files:
      # enumerate dashboard JSON files from observability/grafana/dashboards/

  - name: grafana-provisioning
    namespace: sesam-poc
    options:
      disableNameSuffixHash: true
    files:
      # enumerate provisioning YAML files from observability/grafana/provisioning/
```

Because of `--load-restrictor=LoadRestrictionsNone` (already set in `skaffold.yaml`), kustomize can reference files above `k8s/` without changes.

### Step 3 — Fix Prometheus scrape targets

The docker-compose `prometheus.yml` uses bare hostnames (`ingest`, `writeback`). In K8s, update to use the cluster-internal Service DNS:

```yaml
scrape_configs:
  - job_name: inandout-ingest
    static_configs:
      - targets:
          - inandout-ingest.sesam-poc.svc.cluster.local:9090

  - job_name: inandout-writeback
    static_configs:
      - targets:
          - inandout-writeback.sesam-poc.svc.cluster.local:9091
```

Two options for maintaining a single config:
- **Option A (preferred)**: Keep the docker-compose `prometheus.yml` as-is; create a separate `k8s/base/prometheus.yml` with the K8s hostnames. The kustomize ConfigMapGenerator references the K8s variant.
- **Option B**: Use short names (`inandout-ingest:9090`) — these resolve inside the same namespace without the full FQDN.

### Step 4 — Add to `kustomization.yaml` resources

```yaml
resources:
  - ...existing...
  - observability.yaml
```

### Step 5 — Skaffold port-forwards

Add to `skaffold.yaml`:

```yaml
portForward:
  - resourceType: Service
    resourceName: prometheus
    namespace: sesam-poc
    port: 9090
    localPort: 9095   # avoid collision with ingest's 9090 forward

  - resourceType: Service
    resourceName: grafana
    namespace: sesam-poc
    port: 3000
    localPort: 3000

  - resourceType: Service
    resourceName: alertmanager
    namespace: sesam-poc
    port: 9093
    localPort: 9093
```

## Outcome

After these steps, `skaffold dev` will bring up the full stack including observability. Grafana will be accessible at `http://localhost:3000` with pre-provisioned datasources and dashboards. Prometheus will scrape ingest and writeback metrics every 15 s. Alert rules from the in-and-out repo will fire into Alertmanager (which routes to the webhook placeholder until a real channel is configured).

## Out of Scope

- Loki (log aggregation) — deferred; worth adding once the pipeline is stable
- Tempo (distributed tracing) — deferred; OpenTelemetry instrumentation in in-and-out is a separate effort
- Persistent volumes for Prometheus/Grafana — not needed for a PoC; add if the stack is promoted to staging/production
- Alert channel configuration (Slack, PagerDuty) — see open question in [monitoring.md](monitoring.md)

## Related Plans

- [monitoring.md](monitoring.md) — metric definitions, alert runbooks, open questions
- [example-setup.md](../example-setup.md) — overall example architecture and phases
- [gitops.md](gitops.md) — how observability config lands in prod alongside app config
