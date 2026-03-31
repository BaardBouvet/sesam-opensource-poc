# syntax=docker/dockerfile:1
# =============================================================================
# Schema-manager image.
#
# Build context MUST be the workspace root (sesam-opensource-poc), so that
# vendor/ subdirectories, mapping.yaml, and connectors/ are accessible:
#
#   docker build -f docker/schema-manager.Dockerfile -t sesam-schema-manager .
#
# This image provides:
#   - schema-manager: the Python service that reconciles DDL
#   - osi-engine: renders mapping.yaml → matviews SQL
#   - convert_matviews_to_pgtrickle.py: converts matviews → pg_trickle stream SQL
#   - inandout CLI: runs Alembic migrations via `inandout db upgrade`
#   - psql: for ad-hoc SQL if needed
# =============================================================================

# Global ARGs for FROM references — must appear before the first FROM so that
# Docker treats them as build-time variables available to all FROM statements.
ARG OSI_MAPPING_ENGINE
ARG INANDOUT_ENGINE

# ── Stage 1: OSI-mapping engine (Rust binary + convert script) ────────────────
FROM ${OSI_MAPPING_ENGINE} AS osi

# ── Stage 2: In-and-out engine (inandout CLI + Alembic migrations) ────────────
FROM ${INANDOUT_ENGINE} AS inandout

# ── Stage 3: Schema-manager Python build ──────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build
COPY vendor/schema-manager/pyproject.toml .
COPY vendor/schema-manager/schema_manager/ schema_manager/
RUN pip install --no-cache-dir .

# ── Stage 4: Runtime image ────────────────────────────────────────────────────
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/grove/schema-manager"
LABEL org.opencontainers.image.description="Schema lifecycle supervisor for sesam-opensource-poc"

# osi-engine CLI + conversion script
COPY --from=osi /usr/local/bin/osi-engine /usr/local/bin/
COPY --from=osi /usr/local/bin/convert_matviews_to_pgtrickle.py /usr/local/bin/

# inandout CLI + Alembic migrations
# Copy venv to /app/.venv — the entry-point scripts inside the venv have their
# shebang hardcoded to #!/app/.venv/bin/python (set at pip-install time), so the
# destination path must match exactly or the scripts will fail with ENOENT.
COPY --from=inandout /app/.venv /app/.venv
COPY vendor/in-and-out/engine/migrations/ /opt/inandout/migrations/
COPY vendor/in-and-out/engine/alembic.ini /opt/inandout/alembic.ini
ENV INANDOUT_VENV="/app/.venv"

# psql client
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Schema-manager Python packages (installed into system site-packages by builder)
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/schema-manager /usr/local/bin/schema-manager

# Default config (overridden by ConfigMap mounts in prod, file-sync in dev)
COPY vendor/schema-manager/schema-manager.yaml /config/schema-manager.yaml
COPY mapping.yaml /config/mapping.yaml
COPY connectors/ /config/connectors/

# Non-root user
RUN useradd -r -u 1000 -s /sbin/nologin app
USER app

ENTRYPOINT ["schema-manager"]
CMD ["run", "--config", "/config/schema-manager.yaml"]
