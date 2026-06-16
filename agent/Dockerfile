# Stage 1: Build virtual environment
FROM python:3.14-slim AS builder
WORKDIR /workspace

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy workspace configuration and dependency specifications
COPY pyproject.toml uv.lock ./
COPY sre_common/ ./sre_common/
COPY agent/ ./agent/

# Synchronize dependencies for target package
RUN uv sync --frozen --no-dev --package sre-agent

# Stage 2: Runtime image without uv
FROM python:3.14-slim AS runner

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_GCP=false \
    PORT=8080 \
    PATH="/workspace/.venv/bin:$PATH" \
    PYTHONPATH="/workspace/agent/src:/workspace/sre_common/src:$PYTHONPATH"

WORKDIR /workspace

# Install runtime system dependencies (ca-certificates for HTTPS, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /workspace/.venv /workspace/.venv

# Copy application code and shared library
COPY sre_common/ ./sre_common/
COPY agent/ ./agent/

# Expose port
EXPOSE 8080

# Command to run the agent HTTP service
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8080"]

