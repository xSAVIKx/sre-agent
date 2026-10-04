# GENERATED from sre_agent/src/sre_agent/inventory_client.py by scripts/sync_skill.py - do not edit.
# Change the engine module instead, then run: uv run python scripts/sync_skill.py
"""Fetches a project's topology from the Inventory Agent over A2A."""

from typing import Any

from sre_common.a2a_client import call_agent

from .config import INVENTORY_AGENT_URL


async def fetch_topology(project_id: str, refresh: bool = False, *, fail_fast: bool = False) -> dict[str, Any]:
    """Asks the Inventory Agent (its `get_topology` skill) for the project's topology.

    Returns:
        The topology data artifact: ``status`` (ACTIVE, DISCOVERING or FAILED),
        ``discovered_resources`` ({"services": [...], "databases": [...]}) and
        ``aggregated_metadata``. Empty if the agent returned no data.

    Args:
        project_id: The GCP project.
        refresh: Ask the Inventory Agent to rescan the project.
        fail_fast: Don't retry an unreachable agent (it is optional in mock mode).
    """
    result = await call_agent(
        INVENTORY_AGENT_URL,
        f"Topology of project {project_id}",
        {"project_id": project_id, "refresh": refresh},
        timeout=30.0,
        retry_connect=not fail_fast,
    )
    return next((d for d in result.data if isinstance(d, dict)), {})
