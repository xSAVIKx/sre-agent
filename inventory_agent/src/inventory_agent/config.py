"""Runtime configurations for the Inventory Agent."""

import os

# Base configurations
PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project"
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# Cloud Run Job configurations for asset discovery tasks
SCANNER_JOB_NAME = os.getenv("SCANNER_JOB_NAME", "inventory-scanner-job")
SCANNER_JOB_REGION = os.getenv("SCANNER_JOB_REGION", "us-central1")

# The URL other agents reach this service at, advertised in its A2A agent card.
A2A_PUBLIC_URL = os.getenv("A2A_PUBLIC_URL", f"http://localhost:{os.getenv('PORT', '8080')}")
