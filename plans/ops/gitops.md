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

### GitOps Operator — ArgoCD (vs FluxCD)

ArgoCD watches Git repositories and continuously reconciles the desired state declared in Git with the live state in Kubernetes.

| Dimension | ArgoCD | FluxCD |
|---|---|---|
| **UI** | Full web UI with sync status, diff view, rollback | CLI-first; web UI via Weave GitOps (separate install) |
| **Mental model** | `Application` CRs — explicit, operator-managed apps | Source + Kustomization CRs — more composable, more verbose |
| **Kustomize** | First-class, built-in | First-class, built-in |
| **Validation hooks** | PreSync/PostSync hooks as K8s Jobs — fits validation gates naturally | Uses `dependsOn` + health checks, no dedicated pre-sync Job concept |
| **RBAC** | Built-in multi-tenant RBAC in ArgoCD server | Relies on K8s RBAC directly — less centralized |
| **Rollback UX** | One click in UI / one `argocd app rollback` command | Git revert → push → wait for reconciliation |
| **Secret management** | Delegates to ESO/Sealed Secrets/SOPS | Same; also has native SOPS decryption support |

ArgoCD is chosen because:

- Both [in-and-out](https://github.com/grove/in-and-out) connectors and [OSI-mapping](https://github.com/BaardBouvet/OSI-mapping) configs are **fully declarative YAML** — a natural fit for Git-driven deployment
- The **PreSync hook model** maps directly to our validation gate requirements (validate connectors and compile mappings before allowing sync). FluxCD has no direct equivalent — validation would be limited to CI only, losing the in-cluster pre-deploy safety net.
- **Rollback** for mapping changes that alter SQL views is one command (`argocd app rollback`) vs git-revert-push-wait in FluxCD
- The **web UI** provides immediate value for a small team — seeing which connector version is live in staging vs production at a glance
- Application-of-applications pattern scales cleanly as we add more connectors and targets
- FluxCD's advantage (native SOPS decryption) can be matched in ArgoCD with a SOPS plugin

### Overlay Mechanism — Kustomize (vs Helm)

Kustomize is preferred over Helm for this project because:

- Connector and mapping configs are plain YAML files, not parameterized templates — Kustomize patches are a more natural fit than Go templates
- Lower complexity: no chart packaging, no Tiller history, no `_helpers.tpl`
- Skaffold already supports Kustomize as a renderer ([ADR-003](../adrs/003-deployment-strategy.md))
- Per-environment variations are small (credentials, polling intervals, API base URLs) — strategic merge patches handle these cleanly

#### Why not Helm for release management?

Helm's release history (`helm history`) and `values.yaml`-per-environment model look appealing for versioning, but:

- **ArgoCD already provides release history** — `argocd app history` gives timestamped sync log with exact Git revisions, who triggered it, and success/failure. Helm's release history would be redundant.
- **Helm treats a chart as an atomic release.** This project has three artifact types that need to be promoted independently (images, connector configs, mapping configs). Bundling all three in one Helm chart muddies the audit trail — bumping an image tag creates a new release revision that also "touches" connector and mapping config. Splitting into three charts adds coordination overhead without payoff.
- **Kustomize's model** — each file in Git is its own change — maps cleanly to independent promotion of the three artifact types.

The only scenario that would warrant Helm is distributing the deployment as a reusable package for external users to install. That doesn't apply here.

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

## Release Management

Three distinct artifact types, each with different release and versioning mechanics:

| Artifact | What changes | How versioned | Promotion mechanism |
|---|---|---|---|
| **Container images** | in-and-out service code, OSI-mapping engine | Immutable image tag (e.g. `ghcr.io/grove/inandout:0.4.2`) pinned in the Kustomize base | Bump `newTag` in a PR |
| **Connector configs** | `hubspot.yaml`, `tripletex.yaml` in ConfigMap | Git commit SHA — no separate versioning | Direct file edit in a PR |
| **Mapping configs** | `mapping.yaml` in ConfigMap | Git commit SHA + schema compilation step | Direct file edit in a PR |

These can and should be promoted **independently**. A connector polling interval change doesn't require a new image build.

### Image Upgrades

GitHub Actions builds images and pushes to GHCR on every tagged release. The PR that bumps the tag in Kustomize is the release PR:

```yaml
# deploy/base/kustomization.yaml
images:
  - name: ghcr.io/grove/inandout
    newTag: 0.4.2          # ← bump this in a PR
```

For fully automated image promotion, **ArgoCD Image Updater** can watch GHCR and commit tag bumps automatically when a new tag matching a semver policy appears (e.g. `>= 0.4.0, < 0.5.0`).

### Connector Config Releases

Pure Git workflow — no image involved:
1. Edit `configmap-connectors.yaml` or the overlay patch
2. PR → CI validates connector schema
3. Merge → ArgoCD PreSync hook validates in-cluster → ConfigMap applied
4. in-and-out supports **runtime config reload** (pause, resume, reconfigure without restarting), so connector changes may not need a pod restart

### Mapping Releases

More consequential — a mapping change rebuilds SQL views:
1. Edit `configmap-mappings.yaml`
2. PR → CI runs OSI-mapping `validate` (11 passes)
3. Merge → ArgoCD PreSync Job: `validate` + `create-tables --dry-run`
4. Sync wave 1: apply new ConfigMap
5. Sync wave 2: run `create-tables` Job against the live database
6. Post-sync: pg_trickle `health_check()` confirms stream tables rebuilt cleanly

**Schema compatibility:** The OSI-mapping engine classifies changes as additive (new fields), compatible (field rename with alias), or breaking (removed identity field). Breaking changes should be gated manually even in staging — use `argocd app sync --dry-run` to review.

## Rollout

ArgoCD itself doesn't do canary/blue-green — that's handled at the K8s layer or by the application:

- **in-and-out**: Stateless (state lives in PostgreSQL), so running old and new pods simultaneously during a rolling update is safe. Standard Kubernetes `RollingUpdate` strategy works cleanly.
- **Mapping changes**: Not rolling — a mapping apply Job either succeeds or fails atomically. There's no half-applied mapping. Previous views remain intact until the Job succeeds.
- **pg_trickle**: CloudNativePG handles Postgres upgrades; out of scope for ArgoCD rollout.

## Rollback

### Config rollback (connector or mapping change)

```bash
# See history
argocd app history sesam-production

# Rollback to previous sync
argocd app rollback sesam-production <revision-id>
```

ArgoCD re-applies the exact YAML from the previous Git revision. For mapping configs, this triggers the mapping apply Job with the old version, recreating the previous SQL views. This is the **fastest path** — no git revert needed.

Alternatively: `git revert <sha>` + push → ArgoCD auto-syncs (preferred for auditability in production).

### Image rollback

Revert the tag-bump commit in Git, or select the previous history entry in ArgoCD UI → Rollback.

### Mapping rollback with data implications

If a mapping change altered **identity fields** (changing how records link), rolling back the views doesn't undo already-merged golden records in PostgreSQL. Recovery requires:

1. Roll back the mapping (views revert)
2. Re-run the OSI-mapping engine to recompute golden records from staging tables
3. Re-run any write-back that went out based on the incorrect merge results

This is a **destructive-ish operation** and the reason breaking mapping changes should require **manual approval** in ArgoCD production sync policy. Mitigation: keep staging data a close replica of production to catch bad merges before they reach prod.

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
