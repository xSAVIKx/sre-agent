# GENERATED from sre_agent/src/sre_agent/config.py by scripts/sync_skill.py - do not edit.
# Change the engine module instead, then run: uv run python scripts/sync_skill.py
"""Configuration settings for the SRE Diagnostics Agent."""

import os

# Base configurations
PROJECT_ID = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project"
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# URL of the peer Inventory Agent service
INVENTORY_AGENT_URL = os.getenv("INVENTORY_AGENT_URL", "http://inventory-agent:8080")

# The URL other agents reach this service at, advertised in its A2A agent card.
# deploy.sh / docker-compose set it; the default suits a local `uvicorn --port 8080`.
A2A_PUBLIC_URL = os.getenv("A2A_PUBLIC_URL", f"http://localhost:{os.getenv('PORT', '8080')}")
