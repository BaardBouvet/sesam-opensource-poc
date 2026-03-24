FROM postgres:18.3

ARG PG_TRICKLE_VERSION=0.9.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl git sudo; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) artifact_arch="linux-amd64" ;; \
      arm64) artifact_arch="linux-arm64" ;; \
      *) echo "Unsupported architecture: ${arch}"; exit 1 ;; \
    esac; \
    artifact="pg_trickle-${PG_TRICKLE_VERSION}-pg18-${artifact_arch}.tar.gz"; \
    release_url="https://github.com/grove/pg-trickle/releases/download/v${PG_TRICKLE_VERSION}"; \
    curl -fsSL "${release_url}/${artifact}" -o "/tmp/${artifact}"; \
    curl -fsSL "${release_url}/SHA256SUMS.txt" -o /tmp/SHA256SUMS.txt; \
    cd /tmp; \
    grep " ${artifact}$" SHA256SUMS.txt | sha256sum -c -; \
    tar -xzf "/tmp/${artifact}" -C /tmp; \
    unpacked_dir="/tmp/${artifact%.tar.gz}"; \
    cp "${unpacked_dir}/lib/pg_trickle.so" "$(pg_config --pkglibdir)/"; \
    cp "${unpacked_dir}/extension/"* "$(pg_config --sharedir)/extension/"; \
    rm -rf /tmp/SHA256SUMS.txt "/tmp/${artifact}" "${unpacked_dir}"; \
    useradd -m -s /bin/bash vscode; \
    echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode; \
    chmod 0440 /etc/sudoers.d/vscode

COPY .devcontainer/db/init/00_enable_pgtrickle.sql /docker-entrypoint-initdb.d/

CMD ["postgres", "-c", "shared_preload_libraries=pg_trickle", "-c", "max_worker_processes=16"]
