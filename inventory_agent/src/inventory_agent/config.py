"""Runtime configurations for the Inventory Agent.
"""

import os

# Base configurations
PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project"
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# Cloud Run Job configurations for asset discovery tasks
SCANNER_JOB_NAME = os.getenv("SCANNER_JOB_NAME", "inventory-scanner-job")
SCANNER_JOB_REGION = os.getenv("SCANNER_JOB_REGION", "us-central1")
