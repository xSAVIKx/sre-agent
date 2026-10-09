# Shared runtime base for all four services: Python plus a prebuilt virtual
# environment holding every third-party dependency in uv.lock.
#
# The service images are `FROM` this one and only add their source code, so a
# deploy no longer re-resolves and re-installs the same ~300 MB of wheels four
# times. Published to ghcr.io/xsavikx/sre-agent-base:lock-<hash> by
# .github/workflows/base-image.yml; scripts/base-image.sh computes the hash
# (of uv.lock and this file).

# The Python image comes from mirror.gcr.io: Google's free cache of popular Docker Hub images (the
# same tags and digests). Docker Hub limits anonymous pulls, and shared CI runners get 429 errors.

# Stage 1: resolve and install the dependencies with uv
FROM mirror.gcr.io/library/python:3.14-slim AS builder
WORKDIR /workspace

# Keep in step with UV_VERSION in .github/workflows/ci.yml.
RUN pip install --no-cache-dir uv==0.12.23

# Only the dependency metadata: source changes must not invalidate this layer.
COPY pyproject.toml uv.lock ./
COPY app/pyproject.toml app/pyproject.toml
COPY agent/pyproject.toml agent/pyproject.toml
COPY sre_agent/pyproject.toml sre_agent/pyproject.toml
COPY inventory_agent/pyproject.toml inventory_agent/pyproject.toml
COPY sre_common/pyproject.toml sre_common/pyproject.toml

# Every member's dependencies, but not the members themselves: their code is
# copied by each service image and found through PYTHONPATH.
RUN uv sync --frozen --no-dev --all-packages --no-install-workspace

# Stage 2: runtime image without uv
FROM mirror.gcr.io/library/python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_GCP=false \
    PORT=8080 \
    PATH="/workspace/.venv/bin:$PATH" \
    PYTHONPATH="/workspace/agent/src:/workspace/sre_agent/src:/workspace/inventory_agent/src:/workspace/sre_common/src:/workspace"

WORKDIR /workspace

# ca-certificates for HTTPS, curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /workspace/.venv /workspace/.venv

EXPOSE 8080
