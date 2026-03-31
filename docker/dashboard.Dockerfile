# syntax=docker/dockerfile:1
# =============================================================================
# Sesam Dashboard image.
# Build context MUST be the workspace root (sesam-opensource-poc).
#
#   docker build -f docker/dashboard.Dockerfile -t sesam-dashboard .
# =============================================================================

FROM python:3.13-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY dashboard/pyproject.toml dashboard/pyproject.toml
COPY dashboard/src/ dashboard/src/
RUN uv pip install --system --no-cache dashboard/

FROM python:3.13-slim AS runtime
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/sesam-dashboard /usr/local/bin/sesam-dashboard

EXPOSE 8888
CMD ["sesam-dashboard", "--listen", "0.0.0.0:8888"]
