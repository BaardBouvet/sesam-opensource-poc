# Devcontainer

Developer tooling container for `sesam-opensource-poc`. Provides all the tools needed to build and run the full pipeline locally via a **kind Kubernetes cluster** managed by **Skaffold**.

PostgreSQL does **not** run inside the devcontainer — it runs as a StatefulSet inside the kind cluster (image: `postgres-pgtrickle`, built from `vendor/pg-trickle/Dockerfile.hub`).

## What is pre-installed

| Tool | Purpose |
|---|---|
| `kubectl` | Manage the kind cluster |
| `skaffold` | Build images + deploy to kind |
| `kind` | Local Kubernetes cluster (Docker-in-Docker) |
| `kustomize` | Render K8s overlays |
| `just` | Task runner (`just --list` to see all recipes) |
| `uv` | Python package manager (for in-and-out dev) |
| `cargo` / `rustup` | Rust toolchain (for osi-mapping dev) |
| `psql` | PostgreSQL client (connects to the in-cluster pod) |

## First-time setup

1. Open the repository in VS Code.
2. Run **Dev Containers: Reopen in Container** — this builds the tooling image and installs all tools.
3. Once inside the container, bootstrap the cluster:

```bash
just bootstrap
# Equivalent to: just cluster-create && just submodules && just deploy
```

4. Confirm everything is running:

```bash
just status
```

5. Connect to the in-cluster postgres (Skaffold forwards port 5432):

```bash
just psql
```

## Daily workflow

```bash
just dev          # start Skaffold in watch mode (rebuild + redeploy on save)
just logs-ingest  # tail ingest container logs
just logs-sim     # tail simulator logs
just undeploy     # tear down all cluster resources
```

## Loading the examples

With the cluster running and port 5432 forwarded:

```bash
psql -v ON_ERROR_STOP=1 -f examples/person_with_orders/seed.sql
psql -v ON_ERROR_STOP=1 -f examples/person_with_orders/pgtrickle.sql
```

## Reset

To start completely fresh:

```bash
just cluster-delete
just cluster-create
just deploy
```

## db/

The `db/init/` directory contains a legacy SQL init script from when postgres ran directly inside the devcontainer. It is **no longer used** — `CREATE EXTENSION pg_trickle` is now handled automatically by the `Dockerfile.hub` image (baked into `docker-entrypoint-initdb.d/`). The file is kept for reference only.
