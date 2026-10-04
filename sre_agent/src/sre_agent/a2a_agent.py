"""The SRE diagnostics engine as an A2A agent.

`SreDiagnosticsAgent` is a custom ADK agent that runs the diagnosis pipeline
(`sre_agent.diagnosis`) and reports it as ADK events. ADK's `to_a2a()` serves it
over the Agent2Agent protocol (A2A v1.0):

* the agent card is published at ``/.well-known/agent-card.json``;
* each progress step arrives as a ``TASK_STATE_WORKING`` status update;
* the Markdown report is the task's artifact, followed by ``TASK_STATE_COMPLETED``.

Callers pass the target project and a topology-refresh flag as request metadata:
``{"project_id": "...", "refresh": true}``. The A2A ``contextId`` identifies the
conversation, so repeated diagnoses in one chat share a session history.
"""

from collections.abc import AsyncGenerator
from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from starlette.applications import Starlette

from sre_agent.diagnosis import run_diagnosis

# Where ADK's A2A request converter puts the request metadata in the run config.
A2A_METADATA_KEY = "a2a_metadata"


class SreDiagnosticsAgent(BaseAgent):
    """Diagnoses distributed-system incidents from traces, logs and metrics."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        custom_metadata = (ctx.run_config.custom_metadata if ctx.run_config else None) or {}
        metadata: dict[str, Any] = custom_metadata.get(A2A_METADATA_KEY) or {}
        prompt = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []))

        async for update in run_diagnosis(
            prompt=prompt,
            project_id=metadata.get("project_id"),
            refresh=bool(metadata.get("refresh", False)),
            conversation_id=ctx.session.id,
        ):
            # Every event becomes a WORKING status update. ADK's A2A executor turns the
            # last one - the Report, always yielded last - into the task artifact.
            yield Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                content=types.Content(role="model", parts=[types.Part(text=update.text)]),
            )


sre_diagnostics_agent = SreDiagnosticsAgent(
    name="sre_diagnostics",
    description=(
        "SRE diagnostics engine: analyzes the distributed traces, logs and metrics of a GCP project "
        "and returns a root-cause report with an incident post-mortem."
    ),
)


def build_agent_card(public_url: str) -> AgentCard:
    """The agent's public contract: who it is, where to reach it, and what it can do.

    Args:
        public_url: The base URL clients reach the service at, e.g.
            ``https://sre-sub-agent-123.us-central1.run.app`` on Cloud Run.
    """
    return AgentCard(
        name=sre_diagnostics_agent.name,
        description=sre_diagnostics_agent.description,
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=public_url.rstrip("/") + "/",
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown"],
        skills=[
            AgentSkill(
                id="diagnose_incident",
                name="Diagnose an incident",
                description=(
                    "Finds the slowest or failing request in recent Cloud Trace data, isolates the bottleneck "
                    "span (inclusive vs. exclusive time), correlates Cloud Logging entries and metrics, and "
                    "writes an incident post-mortem. Request metadata: project_id (GCP project to diagnose), "
                    "refresh (rescan the project topology)."
                ),
                tags=["sre", "observability", "cloud-trace", "post-mortem"],
                examples=["Diagnose the recent latency spikes and generate a post-mortem."],
            )
        ],
    )


def build_a2a_app(public_url: str) -> Starlette:
    """Serves `sre_diagnostics_agent` over A2A with ADK's `to_a2a()`.

    `to_a2a()` registers the JSON-RPC and agent-card routes in the app's lifespan,
    so whoever mounts this app must run that lifespan (see `sre_agent.main`).
    """
    return to_a2a(sre_diagnostics_agent, agent_card=build_agent_card(public_url))
