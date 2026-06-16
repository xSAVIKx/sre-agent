# Stage 1: Build virtual environment
FROM python:3.14-slim AS builder
WORKDIR /workspace

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy workspace configuration and dependency specifications
COPY pyproject.toml uv.lock ./
COPY sre_common/ ./sre_common/
COPY sre_agent/ ./sre_agent/

# Synchronize dependencies for target package
RUN uv sync --frozen --no-dev --package sre-sub-agent

# Stage 2: Runtime image
FROM python:3.14-slim AS runner
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/workspace/.venv/bin:$PATH" \
    PYTHONPATH="/workspace/sre_agent/src:/workspace/sre_common/src:$PYTHONPATH" \
    PORT=8080

WORKDIR /workspace

# Install ca-certificates and curl
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder /workspace/.venv /workspace/.venv
COPY sre_common/ ./sre_common/
COPY sre_agent/ ./sre_agent/

EXPOSE 8080
CMD uvicorn sre_agent.main:app --host 0.0.0.0 --port ${PORT}

