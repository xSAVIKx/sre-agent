"""Fetches a project's topology from the Inventory Agent over A2A."""

import time
from typing import Any

from sre_common.a2a_client import call_agent

from sre_agent.config import INVENTORY_AGENT_URL

# Both the diagnosis pipeline and the workflow's fetch_telemetry node need the topology;
# a settled (ACTIVE) answer is reused briefly instead of asking the Inventory Agent twice.
TOPOLOGY_CACHE_SECONDS = 60.0
_topology_cache: dict[str, tuple[float, dict[str, Any]]] = {}


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
    cached = _topology_cache.get(project_id)
    if not refresh and cached and time.monotonic() - cached[0] < TOPOLOGY_CACHE_SECONDS:
        return cached[1]

    result = await call_agent(
        INVENTORY_AGENT_URL,
        f"Topology of project {project_id}",
        {"project_id": project_id, "refresh": refresh},
        timeout=30.0,
        retry_connect=not fail_fast,
    )
    topology = next((d for d in result.data if isinstance(d, dict)), {})
    if topology.get("status") == "ACTIVE":
        _topology_cache[project_id] = (time.monotonic(), topology)
    return topology
