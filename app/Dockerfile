# Use official lightweight Python 3.14 image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_GCP=false \
    PORT=8080

# Set working directory
WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency configuration
COPY pyproject.toml .

# Install dependencies globally inside the container using uv
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY app/ ./app/

# Expose port
EXPOSE 8080

# Command to run the target application
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
