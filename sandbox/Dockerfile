# Песочница Layla: команды агента и терминала в рабочей копии проекта.
# Всегда есть: Python 3.12 (+pip, venv), Node.js 22 (+npm, npx, corepack), gcc/g++/make, git.
# По желанию (флаги сборки в .env): Go, Rust, Java (JDK 17 + Maven).
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ARG SANDBOX_WITH_GO=false
ARG SANDBOX_WITH_RUST=false
ARG SANDBOX_WITH_JAVA=false
ARG GO_VERSION=1.23.4

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates procps util-linux xz-utils unzip \
    && if [ "$SANDBOX_WITH_JAVA" = "true" ]; then \
        apt-get install -y --no-install-recommends openjdk-17-jdk-headless maven; fi \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && node --version && npm --version

RUN if [ "$SANDBOX_WITH_GO" = "true" ]; then \
        curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-$(dpkg --print-architecture).tar.gz" \
        | tar -C /usr/local -xz; fi

RUN if [ "$SANDBOX_WITH_RUST" = "true" ]; then \
        curl -fsSL https://sh.rustup.rs \
        | RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo sh -s -- -y --profile minimal --no-modify-path \
        && chmod -R a+rX /opt/rustup /opt/cargo; fi

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY layla_sandbox ./layla_sandbox
ENV PYTHONPATH=/app

CMD ["python", "-m", "layla_sandbox.server"]
