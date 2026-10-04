"""The Inventory Agent over A2A (v1.0), written directly against the a2a-sdk.

Unlike the SRE engine (an ADK agent served by ADK's `to_a2a()`), this agent has
no LLM: it answers from the project-topology cache. So it implements the
protocol's own `AgentExecutor` interface instead, which shows what `to_a2a()`
does under the hood:

* the agent card is published at ``/.well-known/agent-card.json``;
* a request carries ``{"project_id": "...", "refresh": false}`` as request
  metadata (or as a data part in the message);
* the reply is a task whose artifact is a single **data part**: the cached
  topology (``status``, ``discovered_resources``, ``aggregated_metadata``).
  A missing or stale cache triggers a background scan and answers DISCOVERING.
"""

import datetime
import json
import logging
from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT
from starlette.routing import BaseRoute

from inventory_agent.config import PROJECT_ID
from inventory_agent.routes import lookup_inventory

logger = logging.getLogger("inventory_agent.a2a")


def _json_safe(value: Any) -> Any:
    """Firestore returns datetimes; A2A data parts carry JSON values only."""
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime.date) else str(v)))


def _request_params(context: RequestContext) -> dict[str, Any]:
    """Request metadata, overridden by any data part in the message."""
    params: dict[str, Any] = dict(context.metadata or {})
    if context.message:
        for data in get_data_parts(list(context.message.parts)):
            if isinstance(data, dict):
                params.update(data)
    return params


class InventoryAgentExecutor(AgentExecutor):
    """Answers topology lookups from the inventory cache."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        params = _request_params(context)
        project_id = params.get("project_id") or PROJECT_ID
        try:
            inventory = await lookup_inventory(project_id, refresh=bool(params.get("refresh", False)))
        except Exception as e:
            logger.exception("Inventory lookup failed")
            await updater.failed(new_text_message(f"Inventory lookup failed: {e}", task.context_id, task.id))
            return

        await updater.add_artifact([new_data_part(_json_safe(inventory))], name="topology")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Inventory lookups finish immediately and cannot be canceled.")


def build_agent_card(public_url: str) -> AgentCard:
    """The Inventory Agent's public contract."""
    return AgentCard(
        name="inventory_agent",
        description=(
            "Discovers and caches the infrastructure topology of a GCP project: its Cloud Run services "
            "(with URLs and regions) and its databases."
        ),
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=public_url.rstrip("/") + "/",
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="get_topology",
                name="Get project topology",
                description=(
                    "Returns the cached topology of a GCP project as a data artifact with status "
                    "(ACTIVE, DISCOVERING or FAILED), discovered_resources {services, databases} and "
                    "aggregated_metadata. A missing or stale cache starts a background scan. "
                    "Request metadata: project_id, refresh (force a rescan)."
                ),
                tags=["inventory", "topology", "cloud-run", "discovery"],
                examples=['{"project_id": "my-project", "refresh": false}'],
            )
        ],
    )


def build_a2a_routes(public_url: str) -> list[BaseRoute]:
    """The agent-card and JSON-RPC routes, ready to add to any Starlette/FastAPI app."""
    card = build_agent_card(public_url)
    handler = DefaultRequestHandler(
        agent_executor=InventoryAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return [*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")]
