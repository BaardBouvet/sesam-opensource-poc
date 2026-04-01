# justfile — common dev tasks for the sesam-opensource-poc.
# Run `just` to list all available recipes. Requires `just` ≥ 1.14.
# https://github.com/casey/just

set dotenv-load := true   # auto-load .env if present

KIND_CLUSTER := "sesam-poc"
NAMESPACE    := "sesam-poc"

# ── First-time setup ──────────────────────────────────────────────────────────

# Bootstrap the full local environment (cluster + deploy)
[group('setup')]
bootstrap: cluster-create submodules seed-images deploy

# Create the kind cluster (skips if already exists)
[group('setup')]
cluster-create:
    kind get clusters | grep -q '^{{KIND_CLUSTER}}$' \
        || kind create cluster --name {{KIND_CLUSTER}}

# Pull external images that aren't built by Skaffold and load them into kind
# (kind load docker-image can't handle multi-platform manifests, so we pipe via ctr import)
[group('setup')]
seed-images:
    docker pull curlimages/curl:latest
    docker save curlimages/curl:latest | docker exec -i {{KIND_CLUSTER}}-control-plane ctr --namespace=k8s.io images import -

# Delete the kind cluster
[group('setup')]
cluster-delete:
    kind delete cluster --name {{KIND_CLUSTER}}

# Initialise / update all git submodules
[group('setup')]
submodules:
    git submodule update --init --recursive

# Install Python dev deps for in-and-out (uses uv)
[group('setup')]
py-deps:
    cd vendor/in-and-out && uv sync --all-packages --all-extras

# ── Development loop ──────────────────────────────────────────────────────────

# Start Skaffold in watch mode — rebuilds + redeploys on source changes
# --cache-artifacts=false: skips Docker Hub manifest fetch for cache hashing
# (avoids "TOOMANYREQUESTS" rate-limit hangs when base images aren't cached locally)
[group('dev')]
dev:
    skaffold dev --kubeconfig=$HOME/.kube/config --cache-artifacts=false

# One-shot build + deploy (no watch)
[group('dev')]
deploy:
    skaffold run --kubeconfig=$HOME/.kube/config --cache-artifacts=false

# Tear down namespace, then redeploy from scratch
[group('dev')]
redeploy:
    kubectl delete namespace {{NAMESPACE}} --ignore-not-found=true --wait=true
    skaffold run --kubeconfig=$HOME/.kube/config --cache-artifacts=false

# Forward all service ports to localhost (VS Code will expose them to the host)
# simulator: http://localhost:6100   ingest: http://localhost:9090
# writeback: http://localhost:9091   schema-manager: http://localhost:9080
# postgres:  localhost:5432
[group('dev')]
forward:
    kubectl -n {{NAMESPACE}} port-forward svc/inandout-simulator 6100:6100 &
    kubectl -n {{NAMESPACE}} port-forward svc/inandout-ingest    9090:9090 &
    kubectl -n {{NAMESPACE}} port-forward svc/inandout-writeback 9091:9091 &
    kubectl -n {{NAMESPACE}} port-forward svc/schema-manager     9080:9080 &
    kubectl -n {{NAMESPACE}} port-forward statefulset/postgres   5432:5432 &
    kubectl -n {{NAMESPACE}} port-forward svc/grafana            3000:3000 &
    kubectl -n {{NAMESPACE}} port-forward svc/sesam-dashboard    8888:8888 &
    wait

# Tear down all deployed resources
[group('dev')]
undeploy:
    skaffold delete --kubeconfig=$HOME/.kube/config

# ── Database ──────────────────────────────────────────────────────────────────

# Open a psql shell to the in-cluster postgres (requires port-forward to be up)
[group('db')]
psql:
    psql postgresql://inandout:${POSTGRES_PASSWORD:-changeme}@localhost:5432/inandout

# Wipe all data and re-run schema setup — no redeploy needed.
# Drops the public schema, then triggers a schema-manager reconcile.
[group('db')]
empty-db:
    kubectl -n {{NAMESPACE}} exec statefulset/postgres -- \
        psql -U inandout -d inandout -c \
        "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    just reconcile

# Trigger the schema-manager to reconcile DDL immediately
[group('db')]
reconcile:
    curl -sf -X POST http://localhost:9080/reconcile

# Promote writeback from shadow mode to running
[group('db')]
promote:
    curl -sf -X POST http://localhost:9080/promote

# Re-run schema setup: bounce the schema-manager so it re-reconciles on startup
[group('db')]
migrate:
    kubectl -n {{NAMESPACE}} rollout restart deployment/schema-manager
    kubectl -n {{NAMESPACE}} rollout status  deployment/schema-manager

# ── Observability ─────────────────────────────────────────────────────────────

# Tail ingest logs
[group('obs')]
logs-ingest:
    kubectl -n {{NAMESPACE}} logs -f deployment/inandout-ingest

# Tail writeback logs
[group('obs')]
logs-writeback:
    kubectl -n {{NAMESPACE}} logs -f deployment/inandout-writeback

# Tail simulate logs
[group('obs')]
logs-sim:
    kubectl -n {{NAMESPACE}} logs -f deployment/inandout-simulator

# Tail schema-manager logs
[group('obs')]
logs-schema-manager:
    kubectl -n {{NAMESPACE}} logs -f deployment/schema-manager

# Show all pod statuses
[group('obs')]
status:
    kubectl -n {{NAMESPACE}} get pods,jobs,statefulsets

# ── Housekeeping ──────────────────────────────────────────────────────────────

# Build all images locally without deploying
[group('utils')]
build:
    skaffold build --kubeconfig=$HOME/.kube/config

# Render the kustomize overlay without applying
[group('utils')]
kustomize-render:
    kustomize build k8s/overlays/dev
