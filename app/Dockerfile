# Stage 1: Build virtual environment
FROM python:3.14-slim AS builder

WORKDIR /workspace

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency configuration
COPY app/pyproject.toml ./pyproject.toml

# Create virtual environment and install dependencies using uv
RUN uv venv .venv && uv pip install --no-cache -r pyproject.toml

# Stage 2: Runtime image without uv
FROM python:3.14-slim AS runner

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_GCP=false \
    PORT=8080 \
    PATH="/workspace/.venv/bin:$PATH"

WORKDIR /workspace

# Install runtime system dependencies (ca-certificates for HTTPS, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /workspace/.venv /workspace/.venv

# Copy application code
COPY app/ ./app/

# Expose port
EXPOSE 8080

# Command to run the target application
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
