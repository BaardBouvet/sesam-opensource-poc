# syntax=docker/dockerfile:1
# =============================================================================
# OSI-mapping engine image.
#
# Build context MUST be the workspace root (not vendor/osi-mapping), so that
# the Rust source and the pg-trickle conversion script can both be included:
#
#   docker build -f docker/osi-mapping.Dockerfile -t osi-mapping-engine .
#
# This image provides:
#   - osi-engine: renders mapping.yaml → PostgreSQL materialized-view SQL
#   - convert_matviews_to_pgtrickle.py: converts matviews → pg_trickle stream SQL
# =============================================================================

# ── Stage 1: Build the Rust binary ────────────────────────────────────────────
FROM rust:1-slim-bookworm AS builder

WORKDIR /build/engine-rs

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only what Cargo needs first (layer-cache the dependency compile step)
COPY vendor/osi-mapping/engine-rs/Cargo.toml vendor/osi-mapping/engine-rs/Cargo.lock* ./
RUN mkdir src && echo 'fn main(){}' > src/main.rs \
    && cargo build --release \
    && rm -rf src

# Now copy the real source and spec file (needed by include_str!) and do the final build
COPY vendor/osi-mapping/engine-rs/src ./src
COPY vendor/osi-mapping/spec ../spec
RUN touch src/main.rs && cargo build --release

# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/BaardBouvet/OSI-mapping"
LABEL org.opencontainers.image.description="OSI-mapping engine + pg_trickle conversion script"

COPY --from=builder /build/engine-rs/target/release/osi-engine /usr/local/bin/osi-engine

# Include the pg-trickle matview → stream-table conversion script
COPY vendor/pg-trickle/scripts/convert_matviews_to_pgtrickle.py \
    /usr/local/bin/convert_matviews_to_pgtrickle.py

RUN chmod +x /usr/local/bin/osi-engine \
    && chmod +x /usr/local/bin/convert_matviews_to_pgtrickle.py

# Non-root user for safety
RUN useradd -r -u 1000 -s /sbin/nologin app
USER app

ENTRYPOINT ["osi-engine"]
