# GENERATED from sre_agent/src/sre_agent/sre_workflow.py by scripts/sync_skill.py - do not edit.
# Change the engine module instead, then run: uv run python scripts/sync_skill.py
"""ADK multi-agent workflow for SRE incident diagnostics.

This module orchestrates two specialized ADK agents:
1. TraceAnalyzerAgent: Identifies latency/errors in traces and extracts the trace ID.
2. LogCorrelatorAgent: Correlates the trace ID with logs and diagnoses the root cause.
"""

import json
import logging
from typing import Any

from .gcp_tools import (
    analyze_trace_cascade,
    generate_post_mortem,
    get_trace_details,
    list_metric_descriptors,
    otel_trace,
    query_logs_by_trace,
    query_metrics,
)
from sre_common import retry_async

# Setup logger
logger = logging.getLogger("sre_workflow")

# Resilient imports for google-adk
try:
    from google.adk import Agent as AdkAgent
    from google.adk import Context
    from google.adk import Workflow as AdkWorkflow
    from google.adk.workflow import START, node

    HAS_ADK = True
except ImportError as e:
    HAS_ADK = False
    logger.warning(
        f"google-adk is not installed or failed to import. Using simulated agent fallbacks. Error: {e}", exc_info=True
    )

    class AdkAgent:  # type: ignore
        """Mock ADK Agent for resilience."""

        def __init__(
            self, name: str, instruction: str, model: str = "gemini-3.8-flash", tools: list[Any] | None = None
        ) -> None:
            self.name = name
            self.instruction = instruction
            self.model = model
            self.tools = tools or []

        async def chat(self, prompt: str) -> Any:
            """Mock chat method."""
            return f"Mock response from {self.name} for: {prompt[:30]}..."

    class AdkWorkflow:  # type: ignore
        """Mock ADK Workflow for resilience."""

        def __init__(self, name: str, edges: list[Any]) -> None:
            self.name = name
            self.edges = edges

    def node(*args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            return func

        if args and callable(args[0]):
            return args[0]
        return decorator

    START = "START"

    class Context:  # type: ignore
        """Mock ADK Context for resilience."""


# 1. Define SRE specialized ADK agents
trace_analyzer = AdkAgent(
    name="trace_analyzer",
    instruction=(
        "You are an SRE trace analyst. You receive the recent requests worth diagnosing, "
        "ranked best candidate first; each has an `incident` kind (error or slow) and a `service`. "
        "Pick the request the user is asking about - if nothing narrows it down, the first one. "
        "Return ONLY its raw 32-character hex traceId. "
        "Do not include any extra text, code block backticks, or explanation."
    ),
    model="gemini-3.8-flash",
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction=(
        "You are a senior SRE debugging assistant. Analyze the trace details "
        "and correlated logs provided. Identify the failing span, the root cause "
        "of the issue (such as connection timeouts, resource exhaustion, or "
        "logic errors), and recommend a mitigation plan. "
        "You have access to tools to query observability metrics (e.g., container CPU or memory utilization) "
        "as well as trace cascade bottleneck analysis and incident post-mortem generation "
        "if you need more context or need to build a post-mortem report. "
        "Call each tool at most once per trace: the system appends the full cascade table and "
        "post-mortem to your answer, so do not repeat them - write the root cause and mitigation."
    ),
    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem],
    model="gemini-3.8-flash",
)


# 2. Orchestrate the diagnostic workflow
@retry_async(max_retries=3, initial_delay=2.0)
@otel_trace("_run_adk_diagnostics")
async def _run_adk_diagnostics(
    traces_json: str, project_id: str | None = None, incident: dict[str, Any] | None = None
) -> str:
    """Runs the real multi-agent ADK reasoning workflow.

    Uses Trace Analyzer and Log Correlator agents to identify the anomalous
    trace and diagnose the underlying incident.

    Args:
        traces_json: The ranked incident candidates the TraceAnalyzer chooses from.
        project_id: Optional GCP project identifier.
        incident: The best candidate; its cascade and post-mortem are appended.

    Returns:
        The markdown diagnosis report from the Log Correlator agent.
    """
    import os

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

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

        trace_id = trace_id.strip()
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
                logger.info(f"Inventory Agent unavailable in mock mode ({e}); using the built-in mock topology.")
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

        msg = types.Content(
            parts=[types.Part.from_text(text=f"Find the failing trace ID in these traces:\n{traces_json}")]
        )
        diagnosis = ""
        async for event in runner.run_async(user_id="sre_user", session_id=session.id, new_message=msg):
            # Only the Log Correlator writes the report. The Trace Analyzer's output is the
            # bare trace ID, which would otherwise be prepended to it.
            if event.author == log_correlator.name and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        diagnosis += part.text

        trace_id = incident.get("traceId") if incident else None

        if trace_id:
            logger.info(f"ADK Workflow completed. Appending cascade analysis and post-mortem for trace: {trace_id}")
            cascade_report = await analyze_trace_cascade(trace_id, project_id)
            post_mortem_report = await generate_post_mortem(trace_id, project_id)
            diagnosis = f"{diagnosis}\n\n{cascade_report}\n\n{post_mortem_report}"

        return diagnosis
    except Exception as e:
        logger.error(f"Error during ADK execution: {e}")
        return f"### Diagnostic Execution Failure\nAn error occurred while executing the ADK workflow: {e}"


@otel_trace("_run_simulated_diagnostics")
async def _run_simulated_diagnostics(incident: dict[str, Any], project_id: str | None = None) -> str:
    """Runs a simulated diagnostics fallback loop.

    Locally parses telemetry from mock data files to produce the report.

    Args:
        incident: The trace summary to diagnose (from `incidents.find_incident`).
        project_id: Optional GCP project identifier.

    Returns:
        A simulated markdown diagnostics report.
    """
    import json

    try:
        failing_trace = incident
        trace_id = failing_trace.get("traceId", "unknown_trace_id")
        logger.info(f"[Simulation] Identified trace ID: {trace_id}")

        # Fetch trace details, logs, and metrics from mock files
        trace_details = await get_trace_details(trace_id, project_id)
        logs = await query_logs_by_trace(trace_id, project_id)
        metrics = await query_metrics(
            filter_expression='metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="sre-chaos-monkey"',
            project_id=project_id,
        )
        db_connections = await query_metrics(
            filter_expression='metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" AND resource.labels.database_id="db-primary"',
            project_id=project_id,
        )

        # Build mock SRE analysis response based on telemetry
        trace_data = json.loads(trace_details)
        log_data = json.loads(logs)

        # Parse CPU utilization
        cpu_info = "No CPU utilization data available."
        try:
            cpu_data = json.loads(metrics)
            if isinstance(cpu_data, list) and len(cpu_data) > 0:
                points = cpu_data[0].get("points", [])
                if points:
                    latest_val = points[-1].get("value", 0)
                    cpu_info = f"{latest_val * 100:.1f}% (Healthy)"
        except Exception as e:
            logger.warning(f"Failed to parse mock CPU metrics in simulation: {e}")

        # Parse DB connection count
        db_conn_info = "No DB connection count data available."
        try:
            db_data = json.loads(db_connections)
            if isinstance(db_data, list) and len(db_data) > 0:
                points = db_data[0].get("points", [])
                if points:
                    latest_val = points[-1].get("value", 0)
                    db_conn_info = f"{latest_val} connections (Warning: Max capacity reached)"
        except Exception as e:
            logger.warning(f"Failed to parse mock DB connection metrics in simulation: {e}")

        error_msg = "Unknown error"
        if isinstance(log_data, list):
            for log in log_data:
                if log.get("severity") in ("ERROR", "CRITICAL"):
                    error_msg = log.get("text_payload") or (log.get("json_payload") or {}).get("message", error_msg)

        # Simulate Itinerary Catalog enrichment in report
        from .itinerary import DEFAULT_TEMPLATES

        catalog_md = "## 🗺️ Enriched Service Catalog\n"
        catalog_md += "Pre-defined diagnostic helper filters mapped via similarity lookup:\n"
        for template in DEFAULT_TEMPLATES:
            if template["resource_type"] == "cloud_run_revision":
                # For sre-chaos-monkey
                metrics = template["helpers"]["metrics"].replace("{service_name}", "sre-chaos-monkey")
                logs = template["helpers"]["logs"].replace("{service_name}", "sre-chaos-monkey")
                catalog_md += "- **Resource**: `sre-chaos-monkey` (cloud_run_revision)\n"
                catalog_md += f"  - Metrics: `{metrics}`\n"
                catalog_md += f"  - Logs: `{logs}`\n"
            elif template["resource_type"] == "datastore_database":
                # For (default)
                metrics = template["helpers"]["metrics"].replace("{database_id}", "(default)")
                logs = template["helpers"]["logs"].replace("{database_id}", "(default)")
                catalog_md += "- **Resource**: `(default)` (datastore_database)\n"
                catalog_md += f"  - Metrics: `{metrics}`\n"
                catalog_md += f"  - Logs: `{logs}`\n"
        catalog_md += "\n"

        # Call the new cascade analysis and post-mortem tools
        cascade_report = await analyze_trace_cascade(trace_id, project_id)
        post_mortem_report = await generate_post_mortem(trace_id, project_id)

        report = (
            f"# 🚨 SRE Incident Diagnosis Report\n\n"
            f"**Anomalous Trace ID**: `{trace_id}`\n"
            f"**Root Service**: `{trace_data.get('root_span', 'gateway')}`\n\n"
            f"## 🔍 Root Cause Analysis\n"
            f"A distributed trace scan identified elevated latencies in trace `{trace_id}`. "
            f"Further investigation into the span hierarchy reveals the child span "
            f"`/api/database` was slow and marked with an error status.\n\n"
            f"Correlating this trace with Cloud Logging logs revealed the following error message:\n"
            f"```\n{error_msg}\n```\n\n"
            f"## 📊 Observability Metrics\n"
            f"- **CPU Utilization (sre-chaos-monkey)**: `{cpu_info}`\n"
            f"- **Database Connections (db-primary)**: `{db_conn_info}`\n\n"
            f"{catalog_md}"
            f"{cascade_report}\n\n"
            f"## 🛠️ Recommended Mitigation\n"
            f"1. **Check Database Health**: Verify that the database instance `db-primary.gcp.internal` is running and accessible.\n"
            f"2. **Verify Firewall Rules**: Ensure VPC firewall settings allow ingress traffic from the backend service subnet on port 5432.\n"
            f"3. **Adjust Connection Pools**: Review backend service connection pool configurations to prevent pool exhaustion.\n\n"
            f"{post_mortem_report}"
        )
        return report
    except Exception as e:
        return f"### Diagnostic Simulation Failure\nFailed to parse telemetry during simulation: {e}"


@otel_trace("run_sre_diagnostics")
async def run_sre_diagnostics(traces_json: str, project_id: str | None = None) -> str:
    """Executes the SRE diagnostic workflow using ADK agents.

    Delegates to the real ADK multi-agent workflow if ADK is installed and an API
    key is configured, otherwise falls back to simulated reasoning.

    Args:
        traces_json: A JSON string containing recent trace summaries.
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A markdown-formatted SRE incident diagnosis report.
    """
    logger.info("Starting SRE diagnostics workflow...")

    # 1. The request worth diagnosing: failures before slowness, never the agents'
    #    own traffic; error logs point at a trace when no trace looks bad (incidents.py).
    from .incidents import find_incident

    incident, candidates = await find_incident(traces_json, project_id)
    if incident is None:
        logger.info("Diagnostics workflow found no anomalous traces or error logs. All systems healthy.")
        return (
            "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."
        )
    logger.info(f"Diagnosing {incident.get('incident')} trace {incident.get('traceId')} ({incident.get('name')})")

    import os

    if HAS_ADK and os.environ.get("GEMINI_API_KEY"):
        return await _run_adk_diagnostics(json.dumps(candidates), project_id, incident)
    return await _run_simulated_diagnostics(incident, project_id)
