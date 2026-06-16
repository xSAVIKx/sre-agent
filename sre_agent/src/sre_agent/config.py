"""Configuration settings for the SRE Diagnostics Agent.
"""

import os

# Base configurations
PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project"
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# URL of the peer Inventory Agent service
INVENTORY_AGENT_URL = os.getenv("INVENTORY_AGENT_URL", "http://inventory-agent:8080")
