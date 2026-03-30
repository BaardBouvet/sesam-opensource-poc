# GitOps Deployment for Connectors & Mappings

Deploy in-and-out connector configs, OSI-mapping files, and service workloads via a GitOps pipeline where **Git is the single source of truth** for all desired state.

## Scope

- GitOps operator selection (ArgoCD vs Flux)
- Repository layout for declarative configs (connectors, mappings, K8s manifests)
- Environment promotion strategy (dev → staging → production)
- Overlay mechanism for per-environment parameterization (Kustomize vs Helm)
- Integration with existing Skaffold dev workflow ([ADR-003](../adrs/003-deployment-strategy.md))
- Validation gates before deployment (connector validation, mapping compilation)
- Secrets management in a GitOps context

## Decision: ArgoCD + Kustomize

### GitOps Operator — ArgoCD

ArgoCD watches Git repositories and continuously reconciles the desired state declared in Git with the live state in Kubernetes. It fits this stack well because:

- Both [in-and-out](https://github.com/grove/in-and-out) connectors and [OSI-mapping](https://github.com/BaardBouvet/OSI-mapping) configs are **fully declarative YAML** — a natural fit for Git-driven deployment
- ArgoCD provides a web UI showing sync status, diff visualization, and rollback — useful for operators who need to see which connector version is deployed where
- Native support for Kustomize overlays, Helm charts, and plain manifests
- Application-of-applications pattern scales cleanly as we add more connectors and targets
- Health checks and sync hooks enable pre-deploy validation steps
- Widely adopted with strong community; more operator-friendly UI than Flux

### Overlay Mechanism — Kustomize

Kustomize is preferred over Helm for this project because:

- Connector and mapping configs are plain YAML files, not parameterized templates — Kustomize patches are a more natural fit than Go templates
- Lower complexity: no chart packaging, no Tiller history
- Skaffold already supports Kustomize as a renderer ([ADR-003](../adrs/003-deployment-strategy.md))
- Per-environment variations are small (credentials, polling intervals, API base URLs) — strategic merge patches handle these cleanly

## Repository Layout

```
deploy/
  base/                           # Shared across all environments
    kustomization.yaml
    inandout/
      deployment.yaml             # in-and-out service Deployment
      service.yaml
      configmap-connectors.yaml   # All connector YAMLs as ConfigMap data
    osimapping/
      job-apply.yaml              # Job: validate + compile + apply mappings
      configmap-mappings.yaml     # Mapping YAML files as ConfigMap data
    pgtrickle/
      cnpg-cluster.yaml           # CloudNativePG Cluster with pg_trickle extension
  overlays/
    dev/
      kustomization.yaml          # Patches: simulator endpoints, relaxed schedules
      connector-patches.yaml      # HubSpot/Tripletex → simulator URLs
      secrets.yaml                # SealedSecret or ExternalSecret refs
    staging/
      kustomization.yaml          # Patches: sandbox API keys, tighter schedules
      connector-patches.yaml
      secrets.yaml
    production/
      kustomization.yaml          # Patches: real endpoints, production credentials
      connector-patches.yaml
      secrets.yaml
  argocd/
    appset.yaml                   # ApplicationSet generating per-env Applications
```

### Connector Configs as ConfigMaps

in-and-out connector YAML files are mounted into the service pod via ConfigMaps:

```yaml
# deploy/base/inandout/configmap-connectors.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: inandout-connectors
data:
  hubspot.yaml: |
    # in-and-out connector definition for HubSpot
    ...
  tripletex.yaml: |
    # in-and-out connector definition for Tripletex
    ...
```

The in-and-out Deployment mounts this ConfigMap at `/etc/inandout/connectors/`.

### Mapping Configs as ConfigMaps

OSI-mapping YAML files follow the same pattern:

```yaml
# deploy/base/osimapping/configmap-mappings.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: osimapping-configs
data:
  mapping.yaml: |
    # OSI-mapping definition
    ...
```

A Kubernetes Job (or ArgoCD sync hook) runs the Rust engine's `validate` and `create-tables` commands against the mapping file before the new version goes live.

## Environment Promotion

```
┌─────────┐   merge to main   ┌──────────┐   manual approve   ┌────────────┐
│   dev   │ ◄──── auto-sync   │ staging  │ ◄──── auto-sync    │ production │
└─────────┘                    └──────────┘                     └────────────┘
     ▲                              ▲                                ▲
     │                              │                                │
  push to                    ArgoCD sync                     ArgoCD sync
  feature branch             from main                       requires approval
```

- **dev**: ArgoCD auto-syncs from feature branches or `main`. Uses simulator endpoints. Fast feedback.
- **staging**: ArgoCD auto-syncs from `main`. Points at sandbox APIs with real-ish data. Validation gate runs connector + mapping validation.
- **production**: ArgoCD syncs from `main` but requires **manual approval** (ArgoCD sync policy: `manual`). Tagged releases recommended.

## Validation Gates

### Pre-merge (CI — GitHub Actions)

1. **Connector validation**: `uv run inandout ingest validate-connector --connector connectors/*.yaml` — runs the in-and-out built-in schema + semantic validator
2. **Mapping validation**: `cargo run -- validate mappings/` — runs the OSI-mapping engine's 11-pass validator
3. **Kustomize build**: `kustomize build deploy/overlays/dev` (and staging, production) — ensures overlays render without errors
4. **Dry-run apply**: `kubectl diff` against a CI cluster or `kubeval`/`kubeconform` for manifest validation

### Pre-deploy (ArgoCD sync hooks)

ArgoCD [PreSync hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/resource_hooks/) run before the main sync wave:

1. **PreSync Job — validate connectors**: Runs in-and-out `validate-connector` against the new ConfigMap content
2. **PreSync Job — compile mappings**: Runs OSI-mapping engine `validate` + `create-tables --dry-run` to confirm the mapping compiles to valid SQL views
3. If either job fails, the sync is blocked and ArgoCD reports the failure

### Post-deploy (ArgoCD health checks)

- in-and-out health endpoint (`/health`) reported as ArgoCD custom health check
- pg_trickle `health_check()` run as a post-sync hook to verify stream tables are healthy after mapping changes

## Secrets Management

Git must never contain plaintext secrets. Options ranked by preference:

1. **External Secrets Operator (ESO)** — syncs secrets from AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault into K8s Secrets. ArgoCD manages the `ExternalSecret` resources; the operator fetches the actual values at runtime.
2. **Sealed Secrets** — encrypt secrets client-side with `kubeseal`; only the cluster can decrypt. Encrypted values can be committed to Git safely.
3. **SOPS + age/KMS** — encrypt secret files in-repo. Kustomize has a SOPS plugin for transparent decryption during rendering.

in-and-out connector files reference secrets via environment variables or K8s Secret volume mounts — the actual API keys and OAuth tokens are never in the connector YAML.

## Integration with Existing Tooling

| Tool | Role in GitOps workflow |
|------|------------------------|
| **Skaffold** | Local dev inner loop — `skaffold dev` for fast iteration. Not used in staging/production. |
| **ArgoCD** | Staging + production deployment. Watches Git, reconciles K8s state. |
| **Kustomize** | Overlay engine for both Skaffold (local) and ArgoCD (remote). Shared base manifests. |
| **GitHub Actions** | CI: runs validation gates, builds container images, pushes to GHCR. |
| **GHCR** | Container registry for in-and-out and OSI-mapping engine images. |
| **Testcontainers** | Integration tests in CI. Not part of deploy pipeline. |
| **CloudNativePG** | PostgreSQL operator managing the pg_trickle-enabled cluster. ArgoCD manages the Cluster CR. |

### Workflow: Connector Config Change

1. Developer edits `deploy/base/inandout/configmap-connectors.yaml` (or the overlay patch)
2. Opens PR → GitHub Actions validates connector schema + Kustomize build
3. PR merged → ArgoCD detects change, runs PreSync validation hook
4. Validation passes → ArgoCD applies new ConfigMap → in-and-out pod restarts (or picks up config via watch)
5. ArgoCD health check confirms service is healthy

### Workflow: Mapping Change

1. Developer edits `deploy/base/osimapping/configmap-mappings.yaml`
2. Opens PR → GitHub Actions validates mapping via OSI-mapping engine
3. PR merged → ArgoCD detects change, runs PreSync compile hook
4. Compile passes → ArgoCD applies new ConfigMap + triggers mapping apply Job
5. Job runs `create-tables` → pg_trickle stream tables rebuild → ArgoCD post-sync health check

## Rollback

ArgoCD maintains a history of every sync. To rollback:

- **UI**: Click the previous successful sync revision → "Rollback"
- **CLI**: `argocd app rollback <app-name>`
- **Git revert**: Revert the commit in Git — ArgoCD auto-syncs to the previous state

For mapping changes that alter SQL views, the rollback Job re-applies the previous mapping version, which regenerates the views.

## Open Questions

- Should we use a single ArgoCD Application per environment, or separate Applications for in-and-out, OSI-mapping, and infrastructure?
- Do we need a dedicated GitOps repo (separate from application source), or is a `deploy/` directory in this repo sufficient for PoC scale?
- Which secrets backend (ESO, Sealed Secrets, SOPS) should we commit to first?
- Should mapping apply Jobs use the OSI-mapping engine container directly, or a wrapper that also handles pg_trickle stream table recreation?

## Related Plans

- [Orchestration](orchestration.md)
- [Monitoring & Observability](monitoring.md)
- [Traceability & Audit](traceability.md)
- [API Gateway & Traffic Control](api-gateway-traffic-control.md)
- [Data Ingestion](../ingest/data-ingestion.md)
- [MDM Rules & Write-Back](../sync/mdm-rules-writeback.md)
