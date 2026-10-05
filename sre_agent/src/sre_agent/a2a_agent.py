"""The SRE diagnostics engine as an A2A agent.

`SreDiagnosticsAgent` is a custom ADK agent that runs one of the engine's skills
(`sre_agent.diagnosis`) and reports it as ADK events. ADK's `to_a2a()` serves it
over the Agent2Agent protocol (A2A v1.0):

* the agent card at ``/.well-known/agent-card.json`` lists the skills:
  ``list_incidents``, ``diagnose_incident`` and ``write_post_mortem``;
* each progress step arrives as a ``TASK_STATE_WORKING`` status update;
* the result is the task's artifact - a Markdown text part, plus a data part with
  the same result as JSON when the skill has one - then ``TASK_STATE_COMPLETED``.

A2A messages do not name a skill, so callers pass it as request metadata, next to
its parameters: ``{"skill": "list_incidents", "project_id": "..."}``. Without one
the agent diagnoses. The A2A ``contextId`` identifies the conversation, so repeated
diagnoses in one chat share a session history.

The card also advertises the A2UI extension. A caller that sends A2UI client
capabilities listing the SRE catalog (`a2ui_surfaces.client_capabilities`) gets
the result as an A2UI surface too: one data part per A2UI message, marked with
the A2UI media type.
"""

from collections.abc import AsyncGenerator
from typing import Any

from a2a.helpers import new_data_part
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentSkill
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT
from google.adk.a2a.converters.part_converter import convert_a2a_part_to_genai_part
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from starlette.applications import Starlette

from sre_agent import a2ui_surfaces
from sre_agent.diagnosis import Report, run_diagnosis, run_list_incidents, run_post_mortem

# Where ADK's A2A request converter puts the request metadata in the run config.
A2A_METADATA_KEY = "a2a_metadata"

LIST_INCIDENTS = "list_incidents"
DIAGNOSE_INCIDENT = "diagnose_incident"
WRITE_POST_MORTEM = "write_post_mortem"


def _skill_run(skill: str, prompt: str, metadata: dict[str, Any], conversation_id: str) -> AsyncGenerator:
    """The pipeline for the requested skill, called with its parameters from the metadata."""
    project_id = metadata.get("project_id") or None
    trace_id = metadata.get("trace_id") or None
    ui = a2ui_surfaces.client_renders_sre_catalog(metadata)
    if skill == LIST_INCIDENTS:
        return run_list_incidents(project_id=project_id, ui=ui)
    if skill == WRITE_POST_MORTEM:
        return run_post_mortem(prompt=prompt, project_id=project_id, trace_id=trace_id, ui=ui)
    if skill not in ("", DIAGNOSE_INCIDENT):
        raise ValueError(
            f"Unknown skill {skill!r}: expected one of {LIST_INCIDENTS}, {DIAGNOSE_INCIDENT}, {WRITE_POST_MORTEM}"
        )
    return run_diagnosis(
        prompt=prompt,
        project_id=project_id,
        refresh=bool(metadata.get("refresh", False)),
        conversation_id=conversation_id,
        trace_id=trace_id,
        ui=ui,
    )


def _parts(update: Any) -> list[types.Part]:
    """The update as genai parts: its text, its structured data, and its A2UI messages.

    Data goes through ADK's own converter, so the executor turns it back into A2A
    data parts; the A2UI media type rides along in the part metadata.
    """
    parts = [types.Part(text=update.text)]
    if isinstance(update, Report):
        if update.data is not None:
            parts.append(convert_a2a_part_to_genai_part(new_data_part(update.data)))
        for message in update.a2ui or []:
            a2ui_part = new_data_part(message)
            a2ui_part.metadata.update({"mimeType": a2ui_surfaces.A2UI_MIME_TYPE})
            parts.append(convert_a2a_part_to_genai_part(a2ui_part))
    return parts


class SreDiagnosticsAgent(BaseAgent):
    """Diagnoses distributed-system incidents from traces, logs and metrics."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        custom_metadata = (ctx.run_config.custom_metadata if ctx.run_config else None) or {}
        metadata: dict[str, Any] = custom_metadata.get(A2A_METADATA_KEY) or {}
        prompt = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []))

        skill = str(metadata.get("skill") or "")
        async for update in _skill_run(skill, prompt, metadata, ctx.session.id):
            # Every event becomes a WORKING status update. ADK's A2A executor turns the
            # last one - the Report, always yielded last - into the task artifact.
            yield Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                content=types.Content(role="model", parts=_parts(update)),
            )


sre_diagnostics_agent = SreDiagnosticsAgent(
    name="sre_diagnostics",
    description=(
        "SRE diagnostics engine: lists the recent incidents of a GCP project, diagnoses their root cause "
        "from distributed traces, logs and metrics, and writes incident post-mortems."
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
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri=a2ui_surfaces.A2UI_EXTENSION_URI,
                    description="Results as A2UI surfaces, for clients that send A2UI client capabilities.",
                    params={"supportedCatalogIds": [a2ui_surfaces.SRE_CATALOG_ID]},
                )
            ],
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown"],
        skills=[
            AgentSkill(
                id=LIST_INCIDENTS,
                name="List recent incidents",
                description=(
                    "Lists the recent failing and slow requests in Cloud Trace, most important first, ignoring "
                    "the agents' own traffic. Fast: no model calls. Returns a Markdown table and the same list "
                    'as data. Request metadata: skill="list_incidents", project_id.'
                ),
                tags=["sre", "observability", "cloud-trace"],
                examples=["What are the latest failures?", "Is anything broken right now?"],
                output_modes=["text/markdown", "application/json"],
            ),
            AgentSkill(
                id=DIAGNOSE_INCIDENT,
                name="Diagnose an incident",
                description=(
                    "Finds the root cause of one incident: picks the request the user asks about (or the most "
                    "important one), isolates the bottleneck span (inclusive vs. exclusive time), correlates "
                    "Cloud Logging entries and metrics with ADK agents, and appends a post-mortem. Request "
                    'metadata: skill="diagnose_incident" (the default), project_id, trace_id (optional), '
                    "refresh (rescan the project topology)."
                ),
                tags=["sre", "observability", "cloud-trace", "root-cause"],
                examples=[
                    "Diagnose the recent latency spikes.",
                    "Why did trace 1c65bf87e4be434ea6d6d7edc1ef8c97 fail?",
                ],
                output_modes=["text/markdown"],
            ),
            AgentSkill(
                id=WRITE_POST_MORTEM,
                name="Write a post-mortem",
                description=(
                    "Writes the incident post-mortem of one trace from its spans and logs: overview, timeline, "
                    "root cause and next steps. With a Gemini key, adds AI-generated analyst notes. Request "
                    'metadata: skill="write_post_mortem", project_id, trace_id (optional: defaults to the most '
                    "important recent incident)."
                ),
                tags=["sre", "post-mortem"],
                examples=["Write the post-mortem for trace 1c65bf87e4be434ea6d6d7edc1ef8c97."],
                output_modes=["text/markdown", "application/json"],
            ),
        ],
    )


def build_a2a_app(public_url: str) -> Starlette:
    """Serves `sre_diagnostics_agent` over A2A with ADK's `to_a2a()`.

    `to_a2a()` registers the JSON-RPC and agent-card routes in the app's lifespan,
    so whoever mounts this app must run that lifespan (see `sre_agent.main`).
    """
    return to_a2a(sre_diagnostics_agent, agent_card=build_agent_card(public_url))
