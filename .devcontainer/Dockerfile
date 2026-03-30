# Developer tooling container for sesam-opensource-poc.
# Provides: kubectl, skaffold, kind, kustomize, uv (Python), Rust, just.
# PostgreSQL is NOT here — it runs as a K8s pod via Skaffold.
#
# Docker-in-Docker is provided by the devcontainer feature in devcontainer.json.

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ARG KUBECTL_VERSION=1.33.0
ARG SKAFFOLD_VERSION=2.14.0
ARG KIND_VERSION=0.27.0
ARG KUSTOMIZE_VERSION=5.6.0
ARG JUST_VERSION=1.40.0
ARG UV_VERSION=0.6.12

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git sudo postgresql-client python3 python3-pip \
        build-essential pkg-config libssl-dev; \
    rm -rf /var/lib/apt/lists/*

# ── kubectl ────────────────────────────────────────────────────────────────────
RUN arch="$(dpkg --print-architecture)"; \
    curl -fsSL "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl" \
        -o /usr/local/bin/kubectl; \
    chmod +x /usr/local/bin/kubectl

# ── skaffold ───────────────────────────────────────────────────────────────────
RUN arch="$(dpkg --print-architecture)"; \
    curl -fsSL "https://storage.googleapis.com/skaffold/releases/v${SKAFFOLD_VERSION}/skaffold-linux-${arch}" \
        -o /usr/local/bin/skaffold; \
    chmod +x /usr/local/bin/skaffold

# ── kind ───────────────────────────────────────────────────────────────────────
RUN arch="$(dpkg --print-architecture)"; \
    curl -fsSL "https://kind.sigs.k8s.io/dl/v${KIND_VERSION}/kind-linux-${arch}" \
        -o /usr/local/bin/kind; \
    chmod +x /usr/local/bin/kind

# ── kustomize ─────────────────────────────────────────────────────────────────
RUN arch="$(dpkg --print-architecture)"; \
    curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_${arch}.tar.gz" \
        | tar -xz -C /usr/local/bin; \
    chmod +x /usr/local/bin/kustomize

# ── just ──────────────────────────────────────────────────────────────────────
RUN arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) just_arch="x86_64-unknown-linux-musl" ;; \
      arm64) just_arch="aarch64-unknown-linux-musl" ;; \
    esac; \
    curl -fsSL "https://github.com/casey/just/releases/download/${JUST_VERSION}/just-${JUST_VERSION}-${just_arch}.tar.gz" \
        | tar -xz -C /usr/local/bin just; \
    chmod +x /usr/local/bin/just

# ── uv (Python package manager) ───────────────────────────────────────────────
RUN curl -fsSL "https://astral.sh/uv/install.sh" | UV_VERSION="${UV_VERSION}" sh; \
    mv /root/.local/bin/uv /usr/local/bin/uv; \
    mv /root/.local/bin/uvx /usr/local/bin/uvx 2>/dev/null || true

# ── Rust ──────────────────────────────────────────────────────────────────────
USER vscode
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH="/home/vscode/.cargo/bin:${PATH}"
USER root
