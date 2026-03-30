# justfile — common dev tasks for the sesam-opensource-poc.
# Run `just` to list all available recipes. Requires `just` ≥ 1.14.
# https://github.com/casey/just

set dotenv-load := true   # auto-load .env if present

KIND_CLUSTER := "sesam-poc"
NAMESPACE    := "sesam-poc"

# ── First-time setup ──────────────────────────────────────────────────────────

# Bootstrap the full local environment (cluster + deploy)
[group('setup')]
bootstrap: cluster-create submodules deploy

# Create the kind cluster (skips if already exists)
[group('setup')]
cluster-create:
    kind get clusters | grep -q '^{{KIND_CLUSTER}}$' \
        || kind create cluster --name {{KIND_CLUSTER}}

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

# Tear down all deployed resources
[group('dev')]
undeploy:
    skaffold delete --kubeconfig=$HOME/.kube/config

# ── Database ──────────────────────────────────────────────────────────────────

# Open a psql shell to the in-cluster postgres (requires port-forward to be up)
[group('db')]
psql:
    psql postgresql://inandout:${POSTGRES_PASSWORD:-changeme}@localhost:5432/inandout

# Re-run the migrate job (delete + recreate with a timestamp suffix)
[group('db')]
migrate:
    kubectl -n {{NAMESPACE}} delete job sesam-migrate --ignore-not-found
    kubectl -n {{NAMESPACE}} create job sesam-migrate-$(date +%s) \
        --from=job/sesam-migrate 2>/dev/null || \
        kubectl apply -k k8s/overlays/dev

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
