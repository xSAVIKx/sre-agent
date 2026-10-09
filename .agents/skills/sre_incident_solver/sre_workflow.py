# GENERATED from sre_agent/src/sre_agent/sre_workflow.py by scripts/sync_skill.py - do not edit.
# Change the engine module instead, then run: uv run python scripts/sync_skill.py
"""ADK multi-agent workflow for SRE incident diagnostics.

This module orchestrates two specialized ADK agents:
1. TraceAnalyzerAgent: Identifies latency/errors in traces and extracts the trace ID.
2. LogCorrelatorAgent: Correlates the trace ID with logs and diagnoses the root cause.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from google.adk import Agent as AdkAgent
from google.adk import Context
from google.adk import Workflow as AdkWorkflow
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import START, node

from .gcp_tools import (
    analyze_trace_cascade,
    generate_post_mortem,
    get_trace_details,
    list_metric_descriptors,
    otel_trace,
    query_logs_by_trace,
    query_metrics,
)
from .simulated_llm import SimulatedLlm
from sre_common import retry_async

logger = logging.getLogger("sre_workflow")

GEMINI_MODEL = "gemini-3.8-flash"


def _model() -> str | BaseLlm:
    """Gemini when GEMINI_API_KEY is set. Else a scripted model, so the same ADK workflow runs offline."""
    return GEMINI_MODEL if os.environ.get("GEMINI_API_KEY") else SimulatedLlm()


MODEL = _model()


# 1. The two ADK agents. Each agent is a model, an instruction and (optionally) tools.
TRACE_ANALYZER_INSTRUCTION = (
    "You are an SRE trace analyst. You receive the recent requests worth diagnosing, "
    "ranked best candidate first; each has an `incident` kind (error or slow) and a `service`. "
    "Pick the request the user is asking about - if nothing narrows it down, the first one. "
    "Return ONLY its raw 32-character hex traceId. "
    "Do not include any extra text, code block backticks, or explanation."
)

LOG_CORRELATOR_INSTRUCTION = (
    "You are a senior SRE debugging assistant. Analyze the trace details "
    "and correlated logs provided. Identify the failing span, the root cause "
    "of the issue (such as connection timeouts, resource exhaustion, or "
    "logic errors), and recommend a mitigation plan. "
    "Always check the metrics before you decide: call list_metric_descriptors to see which metrics "
    "exist, then query_metrics for the metrics of the failing span's service or database (for example "
    "the database connection count or the container CPU). "
    "Call each tool at most once per trace: the system appends the full cascade table and "
    "post-mortem to your answer, so do not repeat them - write the root cause and mitigation. "
    "Answer with three sections: '## 🔍 Root Cause Analysis', '## 📊 Observability Metrics' (the values "
    "that you queried) and '## 🛠️ Recommended Mitigation'. "
    "Base every claim on the spans, logs and metrics you were given. If they do not show why "
    "the bottleneck span was slow or failed (e.g. no error message), say that the cause is not "
    "in the telemetry and what to check; do not infer one from service names."
)

trace_analyzer = AdkAgent(name="trace_analyzer", model=MODEL, instruction=TRACE_ANALYZER_INSTRUCTION)

log_correlator = AdkAgent(
    name="log_correlator",
    model=MODEL,
    instruction=LOG_CORRELATOR_INSTRUCTION,
    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem],
)


@dataclass(frozen=True)
class Diagnosis:
    """A diagnosis report and the trace it diagnosed (None when nothing was wrong).

    `failed` marks a report that only describes why the diagnosis could not run.
    """

    report: str
    trace_id: str | None = None
    failed: bool = False


# 2. Orchestrate the diagnostic workflow
@retry_async(max_retries=3, initial_delay=2.0)
@otel_trace("_run_adk_diagnostics")
async def _run_adk_diagnostics(
    traces_json: str, project_id: str | None = None, incident: dict[str, Any] | None = None, question: str = ""
) -> Diagnosis:
    """Runs the real multi-agent ADK reasoning workflow.

    Uses Trace Analyzer and Log Correlator agents to identify the anomalous
    trace and diagnose the underlying incident.

    Args:
        traces_json: The ranked incident candidates the TraceAnalyzer chooses from.
        project_id: Optional GCP project identifier.
        incident: The best candidate, used when the TraceAnalyzer's pick is not a candidate.
        question: The user's request, so the TraceAnalyzer can pick the incident it is about.

    Returns:
        The Log Correlator's report, with the cascade and post-mortem of the trace it diagnosed.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    candidate_ids = {c.get("traceId") for c in json.loads(traces_json) if isinstance(c, dict)}
    # The trace the TraceAnalyzer picked; its cascade and post-mortem are appended to the report.
    chosen: dict[str, str] = {}

    @node(name="fetch_telemetry")
    async def fetch_telemetry(ctx: Context, node_input: Any) -> str:
        # Extract trace_id from node_input
        trace_id = ""
        if isinstance(node_input, str):
            trace_id = node_input
        elif hasattr(node_input, "output") and node_input.output is not None:
            trace_id = str(node_input.output)
        elif hasattr(node_input, "parts") and node_input.parts:
            trace_id = "".join(p.text for p in node_input.parts if p.text)
        elif isinstance(node_input, dict) and "output" in node_input:
            trace_id = str(node_input["output"])

        trace_id = trace_id.strip().strip("`")
        if trace_id not in candidate_ids and incident:
            logger.warning(f"TraceAnalyzer picked {trace_id!r}, not a candidate; using {incident.get('traceId')}")
            trace_id = incident.get("traceId", "")
        chosen["trace_id"] = trace_id
        logger.info(f"Workflow: Fetching telemetry for trace ID '{trace_id}'")

        # Load project ID
        proj_id = project_id or os.environ.get("GCP_PROJECT")

        # Fetch topology from Inventory Agent
        topology = {}
        from .config import IS_MOCK

        try:
            from .inventory_client import fetch_topology

            topology = await fetch_topology(proj_id or "mock-project", fail_fast=IS_MOCK)
        except Exception as e:
            if IS_MOCK:
                # Expected in the standalone simulation: no Inventory Agent is running.
                logger.info("No Inventory Agent in this local run: using the built-in mock topology.")
                logger.debug(f"Inventory Agent error: {e}")
            else:
                logger.error(f"Failed to query Inventory Agent: {e}")

        # Fallback topology in mock mode
        if IS_MOCK and not topology.get("discovered_resources"):
            topology = {
                "discovered_resources": {
                    "services": [
                        {
                            "name": "sre-chaos-monkey",
                            "url": "https://sre-chaos-monkey-mock.run.app",
                            "vpc_connector": "sre-vpc",
                        },
                        {"name": "sre-agent", "url": "https://sre-agent-mock.run.app"},
                    ],
                    "databases": [{"name": "(default)", "type": "FIRESTORE"}],
                }
            }

        # Initialize Firestore and seed
        from .firestore_strategy import _get_db
        from .itinerary import find_matching_template, seed_templates_if_empty

        db = await _get_db()
        if db is not None:
            await seed_templates_if_empty(db)

        # Enrich discovered topology resources
        enriched_catalog = []

        # Helper to map database type to GCP resource type
        def get_db_resource_type(db_type: str) -> str:
            db_type = db_type.upper()
            if "FIRESTORE" in db_type or "DATASTORE" in db_type:
                return "datastore_database"
            elif "SQL" in db_type or "POSTGRES" in db_type or "MYSQL" in db_type:
                return "cloudsql_database"
            return "cloudsql_database"

        services = topology.get("discovered_resources", {}).get("services", [])
        databases = topology.get("discovered_resources", {}).get("databases", [])

        # One template lookup (an embedding + a vector query) per resource: run them
        # concurrently instead of one after another.
        import asyncio

        lookups = [("cloud_run_revision", f"service: {svc.get('name')}, type: cloud_run_revision") for svc in services]
        lookups += [
            (
                get_db_resource_type(d.get("type", "FIRESTORE")),
                f"database: {d.get('name')}, type: {get_db_resource_type(d.get('type', 'FIRESTORE'))}",
            )
            for d in databases
        ]
        templates = await asyncio.gather(*(find_matching_template(db, rt, q) for rt, q in lookups))
        service_templates = templates[: len(services)]
        database_templates = templates[len(services) :]

        for svc, template in zip(services, service_templates, strict=True):
            svc_name = svc.get("name")
            resource_type = "cloud_run_revision"
            if template:
                helpers = template.get("helpers", {})
                metrics = helpers.get("metrics", "").replace("{service_name}", svc_name)
                logs = helpers.get("logs", "").replace("{service_name}", svc_name)
                enriched_catalog.append(
                    {
                        "resource_name": svc_name,
                        "resource_type": resource_type,
                        "suggested_metrics_query": metrics,
                        "suggested_logs_query": logs,
                    }
                )

        for db_res, template in zip(databases, database_templates, strict=True):
            db_name = db_res.get("name")
            resource_type = get_db_resource_type(db_res.get("type", "FIRESTORE"))
            if template:
                helpers = template.get("helpers", {})
                metrics = helpers.get("metrics", "").replace("{database_id}", db_name)
                logs = helpers.get("logs", "").replace("{database_id}", db_name)
                enriched_catalog.append(
                    {
                        "resource_name": db_name,
                        "resource_type": resource_type,
                        "suggested_metrics_query": metrics,
                        "suggested_logs_query": logs,
                    }
                )

        enriched_catalog_md = ""
        if enriched_catalog:
            enriched_catalog_md = "\n=== Enriched Service Catalog (Pre-defined Diagnostic Helpers) ===\n"
            enriched_catalog_md += "Use these pre-defined queries when using your query_metrics or logging tools instead of inventing them:\n"
            for item in enriched_catalog:
                enriched_catalog_md += f"- **Resource**: `{item['resource_name']}` ({item['resource_type']})\n"
                if item["suggested_metrics_query"]:
                    enriched_catalog_md += f"  - Suggested Metrics Filter: `{item['suggested_metrics_query']}`\n"
                if item["suggested_logs_query"]:
                    enriched_catalog_md += f"  - Suggested Logs Filter: `{item['suggested_logs_query']}`\n"
            enriched_catalog_md += "\n"

        # Fetch telemetry
        trace_details = await get_trace_details(trace_id, proj_id)
        logs = await query_logs_by_trace(trace_id, proj_id)

        analysis_prompt = (
            f"Trace Spans:\n{trace_details}\n\n"
            f"Correlated Logs:\n{logs}\n\n"
            f"{enriched_catalog_md}"
            f"Provide a root cause analysis and mitigation plan."
        )
        return analysis_prompt

    try:
        # Define the ADK 2.0 graph workflow
        sre_diagnostics_workflow = AdkWorkflow(
            name="sre_diagnostics_workflow", edges=[(START, trace_analyzer, fetch_telemetry, log_correlator)]
        )

        session_service = InMemorySessionService()
        runner = Runner(node=sre_diagnostics_workflow, app_name="sre_diagnostics", session_service=session_service)

        # Create session before running (InMemorySessionService requires explicit creation)
        session = await session_service.create_session(
            app_name="sre_diagnostics",
            user_id="sre_user",
        )

        request = f"Find the failing trace ID in these traces:\n{traces_json}"
        if question:
            request = f"The user asked: {question}\n\n{request}"
        msg = types.Content(parts=[types.Part.from_text(text=request)])
        diagnosis = ""
        async for event in runner.run_async(user_id="sre_user", session_id=session.id, new_message=msg):
            # Only the Log Correlator writes the report. The Trace Analyzer's output is the
            # bare trace ID, which would otherwise be prepended to it.
            if event.author == log_correlator.name and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        diagnosis += part.text

        trace_id = chosen.get("trace_id") or (incident.get("traceId") if incident else None)

        if trace_id:
            logger.info(f"ADK Workflow completed. Appending cascade analysis and post-mortem for trace: {trace_id}")
            cascade_report = await analyze_trace_cascade(trace_id, project_id)
            post_mortem_report = await generate_post_mortem(trace_id, project_id)
            diagnosis = f"{diagnosis}\n\n{cascade_report}\n\n{post_mortem_report}"

        return Diagnosis(diagnosis, trace_id)
    except Exception as e:
        logger.error(f"Error during ADK execution: {e}")
        return Diagnosis(
            f"### Diagnostic Execution Failure\nAn error occurred while executing the ADK workflow: {e}", failed=True
        )


async def run_sre_diagnostics(
    traces_json: str, project_id: str | None = None, question: str = "", trace_id: str | None = None
) -> str:
    """Executes the SRE diagnostic workflow and returns its Markdown report (see `diagnose`)."""
    return (await diagnose(traces_json, project_id, question, trace_id)).report


@otel_trace("run_sre_diagnostics")
async def diagnose(
    traces_json: str, project_id: str | None = None, question: str = "", trace_id: str | None = None
) -> Diagnosis:
    """Executes the SRE diagnostic workflow using ADK agents.

    The agents use Gemini when GEMINI_API_KEY is set, and the scripted
    `SimulatedLlm` otherwise (see `MODEL`).

    Args:
        traces_json: A JSON string containing recent trace summaries.
        project_id: The GCP Project ID. If None, uses default configuration.
        question: The user's request; the TraceAnalyzer uses it to pick the incident.
        trace_id: Diagnose this trace instead of picking one.

    Returns:
        The Markdown diagnosis report and the trace it diagnosed.
    """
    logger.info("Starting SRE diagnostics workflow...")

    # 1. The request worth diagnosing: failures before slowness, never the agents'
    #    own traffic; error logs point at a trace when no trace looks bad (incidents.py).
    from .incidents import find_incident

    if trace_id:
        from .incidents import parse_traces

        known = next((t for t in parse_traces(traces_json) if t.get("traceId") == trace_id), {})
        incident = {"traceId": trace_id, "incident": "requested", **known}
        candidates = [incident]
    else:
        incident, candidates = await find_incident(traces_json, project_id)
    if incident is None:
        logger.info("Diagnostics workflow found no anomalous traces or error logs. All systems healthy.")
        return Diagnosis(
            "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."
        )
    logger.info(f"Diagnosing {incident.get('incident')} trace {incident.get('traceId')} ({incident.get('name')})")

    return await _run_adk_diagnostics(json.dumps(candidates), project_id, incident, question)
