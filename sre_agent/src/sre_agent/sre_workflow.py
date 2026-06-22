"""ADK multi-agent workflow for SRE incident diagnostics.

This module orchestrates two specialized ADK agents:
1. TraceAnalyzerAgent: Identifies latency/errors in traces and extracts the trace ID.
2. LogCorrelatorAgent: Correlates the trace ID with logs and diagnoses the root cause.
"""

import logging
from typing import Any
from sre_agent.gcp_tools import get_trace_details, query_logs_by_trace, query_metrics, list_metric_descriptors, otel_trace

# Setup logger
logger = logging.getLogger("sre_workflow")

# Resilient imports for google-adk
try:
    from google.adk import Agent as AdkAgent
    from google.adk import Workflow as AdkWorkflow
    from google.adk.workflow import node, START
    from google.adk import Context
    HAS_ADK = True
except ImportError as e:
    HAS_ADK = False
    logger.warning(
        f"google-adk is not installed or failed to import. Using simulated agent fallbacks. Error: {e}",
        exc_info=True
    )

    class AdkAgent:  # type: ignore
        """Mock ADK Agent for resilience."""
        def __init__(self, name: str, instruction: str, model: str = "gemini-3-flash-preview", tools: list[Any] | None = None) -> None:
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
    class Context: pass  # type: ignore


# 1. Define SRE specialized ADK agents
trace_analyzer = AdkAgent(
    name="trace_analyzer",
    instruction=(
        "You are an SRE trace analyst. Analyze the provided traces list. "
        "Locate the trace representing the slowest or failing request. "
        "Extract its traceId and return ONLY the raw 32-character hex traceId. "
        "Do not include any extra text, code block backticks, or explanation."
    ),
    model="gemini-3-flash-preview"
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction=(
        "You are a senior SRE debugging assistant. Analyze the trace details "
        "and correlated logs provided. Identify the failing span, the root cause "
        "of the issue (such as connection timeouts, resource exhaustion, or "
        "logic errors), and recommend a mitigation plan. "
        "You have access to tools to query observability metrics (e.g., container CPU or memory utilization) "
        "if you need more context to diagnose the problem."
    ),
    tools=[query_metrics, list_metric_descriptors],
    model="gemini-3-flash-preview"
)


# 2. Orchestrate the diagnostic workflow
@otel_trace("_run_adk_diagnostics")
async def _run_adk_diagnostics(traces_json: str, project_id: str | None = None) -> str:
    """Runs the real multi-agent ADK reasoning workflow.

    Uses Trace Analyzer and Log Correlator agents to identify the anomalous
    trace and diagnose the underlying incident.

    Args:
        traces_json: JSON string representing the recent trace summaries.
        project_id: Optional GCP project identifier.

    Returns:
        The markdown diagnosis report from the Log Correlator agent.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    import os

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
        try:
            from sre_agent.config import INVENTORY_AGENT_URL, IS_MOCK
            import httpx
            inv_url = f"{INVENTORY_AGENT_URL}/v1/agents/inventory"
            params = {"project_id": proj_id or "mock-project"}
            async with httpx.AsyncClient() as client:
                resp = await client.get(inv_url, params=params, timeout=15.0)
                if resp.status_code == 200:
                    topology = resp.json()
                else:
                    logger.warning(f"Inventory Agent returned status code: {resp.status_code}")
        except Exception as e:
            logger.error(f"Failed to query Inventory Agent: {e}")

        # Fallback topology in mock mode
        if IS_MOCK and not topology.get("discovered_resources"):
            topology = {
                "discovered_resources": {
                    "services": [
                        {"name": "sre-chaos-monkey", "url": "https://sre-chaos-monkey-mock.run.app", "vpc_connector": "sre-vpc"},
                        {"name": "sre-agent", "url": "https://sre-agent-mock.run.app"}
                    ],
                    "databases": [
                        {"name": "(default)", "type": "FIRESTORE"}
                    ]
                }
            }

        # Initialize Firestore and seed
        from sre_agent.firestore_strategy import _get_db
        from sre_agent.itinerary import seed_templates_if_empty, find_matching_template
        
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
        for svc in services:
            svc_name = svc.get("name")
            resource_type = "cloud_run_revision"
            description_query = f"service: {svc_name}, type: {resource_type}"
            
            template = await find_matching_template(db, resource_type, description_query)
            if template:
                helpers = template.get("helpers", {})
                metrics = helpers.get("metrics", "").replace("{service_name}", svc_name)
                logs = helpers.get("logs", "").replace("{service_name}", svc_name)
                enriched_catalog.append({
                    "resource_name": svc_name,
                    "resource_type": resource_type,
                    "suggested_metrics_query": metrics,
                    "suggested_logs_query": logs
                })

        databases = topology.get("discovered_resources", {}).get("databases", [])
        for db_res in databases:
            db_name = db_res.get("name")
            db_type = db_res.get("type", "FIRESTORE")
            resource_type = get_db_resource_type(db_type)
            description_query = f"database: {db_name}, type: {resource_type}"
            
            template = await find_matching_template(db, resource_type, description_query)
            if template:
                helpers = template.get("helpers", {})
                metrics = helpers.get("metrics", "").replace("{database_id}", db_name)
                logs = helpers.get("logs", "").replace("{database_id}", db_name)
                enriched_catalog.append({
                    "resource_name": db_name,
                    "resource_type": resource_type,
                    "suggested_metrics_query": metrics,
                    "suggested_logs_query": logs
                })

        enriched_catalog_md = ""
        if enriched_catalog:
            enriched_catalog_md = "\n=== Enriched Service Catalog (Pre-defined Diagnostic Helpers) ===\n"
            enriched_catalog_md += "Use these pre-defined queries when using your query_metrics or logging tools instead of inventing them:\n"
            for item in enriched_catalog:
                enriched_catalog_md += f"- **Resource**: `{item['resource_name']}` ({item['resource_type']})\n"
                if item['suggested_metrics_query']:
                    enriched_catalog_md += f"  - Suggested Metrics Filter: `{item['suggested_metrics_query']}`\n"
                if item['suggested_logs_query']:
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
            name="sre_diagnostics_workflow",
            edges=[
                (START, trace_analyzer, fetch_telemetry, log_correlator)
            ]
        )

        session_service = InMemorySessionService()
        runner = Runner(
            node=sre_diagnostics_workflow,
            app_name="sre_diagnostics",
            session_service=session_service
        )

        # Create session before running (InMemorySessionService requires explicit creation)
        session = await session_service.create_session(
            app_name="sre_diagnostics",
            user_id="sre_user",
        )

        msg = types.Content(parts=[types.Part.from_text(text=f"Find the failing trace ID in these traces:\n{traces_json}")])
        diagnosis = ""
        async for event in runner.run_async(
            user_id="sre_user",
            session_id=session.id,
            new_message=msg
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        diagnosis += part.text
        return diagnosis
    except Exception as e:
        logger.error(f"Error during ADK execution: {e}")
        return f"### Diagnostic Execution Failure\nAn error occurred while executing the ADK workflow: {e}"


@otel_trace("_run_simulated_diagnostics")
async def _run_simulated_diagnostics(traces_json: str, project_id: str | None = None) -> str:
    """Runs a simulated diagnostics fallback loop.

    Locally parses telemetry from mock data files to produce the report.

    Args:
        traces_json: JSON string representing the recent trace summaries.
        project_id: Optional GCP project identifier.

    Returns:
        A simulated markdown diagnostics report.
    """
    import json
    try:
        data = json.loads(traces_json)
        # Find the first trace with error = True or slow latency (> 5000ms)
        failing_trace = None
        if isinstance(data, list):
            for t in data:
                name = t.get("name", "").lower()
                if any(x in name for x in ("diagnose", "health", "warmup")) or name == "/":
                    continue
                if t.get("error") is True or t.get("durationMs", 0) > 5000:
                    failing_trace = t
                    break
            if not failing_trace:
                # Check if there are mock logs with ERROR/CRITICAL severity in the database
                from sre_agent.gcp_tools import _load_mock_file
                mock_logs = _load_mock_file("logs.json") or []
                has_error_logs = False
                for log in mock_logs:
                    if log.get("severity") in ("ERROR", "CRITICAL"):
                        has_error_logs = True
                        break
                
                if not has_error_logs:
                    return "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."
                
                # If there are error logs, fallback to first non-diagnose trace to analyze it
                if data:
                    for t in data:
                        name = t.get("name", "").lower()
                        if not (any(x in name for x in ("diagnose", "health", "warmup")) or name == "/"):
                            failing_trace = t
                            break
                    if not failing_trace:
                        failing_trace = data[0]

        if not failing_trace:
            return "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."

        trace_id = failing_trace.get("traceId", "unknown_trace_id")
        logger.info(f"[Simulation] Identified trace ID: {trace_id}")

        # Fetch trace details, logs, and metrics from mock files
        trace_details = await get_trace_details(trace_id, project_id)
        logs = await query_logs_by_trace(trace_id, project_id)
        metrics = await query_metrics(
            filter_expression='metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="sre-chaos-monkey"',
            project_id=project_id
        )
        db_connections = await query_metrics(
            filter_expression='metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" AND resource.labels.database_id="db-primary"',
            project_id=project_id
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
                    error_msg = log.get("text_payload") or log.get("json_payload", {}).get("message", error_msg)

        # Simulate Itinerary Catalog enrichment in report
        from sre_agent.itinerary import DEFAULT_TEMPLATES
        catalog_md = "## 🗺️ Enriched Service Catalog\n"
        catalog_md += "Pre-defined diagnostic helper filters mapped via similarity lookup:\n"
        for template in DEFAULT_TEMPLATES:
            if template["resource_type"] == "cloud_run_revision":
                # For sre-chaos-monkey
                metrics = template["helpers"]["metrics"].replace("{service_name}", "sre-chaos-monkey")
                logs = template["helpers"]["logs"].replace("{service_name}", "sre-chaos-monkey")
                catalog_md += f"- **Resource**: `sre-chaos-monkey` (cloud_run_revision)\n"
                catalog_md += f"  - Metrics: `{metrics}`\n"
                catalog_md += f"  - Logs: `{logs}`\n"
            elif template["resource_type"] == "datastore_database":
                # For (default)
                metrics = template["helpers"]["metrics"].replace("{database_id}", "(default)")
                logs = template["helpers"]["logs"].replace("{database_id}", "(default)")
                catalog_md += f"- **Resource**: `(default)` (datastore_database)\n"
                catalog_md += f"  - Metrics: `{metrics}`\n"
                catalog_md += f"  - Logs: `{logs}`\n"
        catalog_md += "\n"

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
            f"## 🛠️ Recommended Mitigation\n"
            f"1. **Check Database Health**: Verify that the database instance `db-primary.gcp.internal` is running and accessible.\n"
            f"2. **Verify Firewall Rules**: Ensure VPC firewall settings allow ingress traffic from the backend service subnet on port 5432.\n"
            f"3. **Adjust Connection Pools**: Review backend service connection pool configurations to prevent pool exhaustion."
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

    # 1. Parse traces and check if there are any failed or slow traces
    has_problems = False
    try:
        import json
        traces = json.loads(traces_json)
        if isinstance(traces, list):
            for t in traces:
                name = t.get("name", "").lower()
                # Skip system agent paths
                if any(x in name for x in ("diagnose", "health", "warmup")) or name == "/":
                    continue
                if t.get("error") is True or t.get("durationMs", 0) > 5000:
                    has_problems = True
                    break
    except Exception as e:
        logger.warning(f"Failed to parse traces in pre-check: {e}")

    # 2. If traces look clean, check recent logs for ERROR/CRITICAL severity
    if not has_problems:
        logger.info("No anomalous traces found. Checking logs for recent errors...")
        try:
            from sre_agent.gcp_tools import query_logs
            log_res = await query_logs(query="severity=ERROR OR severity=CRITICAL", project_id=project_id, limit=5)
            logs = json.loads(log_res)
            if isinstance(logs, list) and len(logs) > 0:
                for log in logs:
                    if log.get("severity") in ("ERROR", "CRITICAL"):
                        has_problems = True
                        break
        except Exception as e:
            logger.warning(f"Failed to query logs in pre-check: {e}")

    # 3. If everything is healthy, return clean report
    if not has_problems:
        logger.info("Diagnostics workflow found no anomalous traces or error logs. All systems healthy.")
        return "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."

    import os
    if HAS_ADK and "GEMINI_API_KEY" in os.environ:
        return await _run_adk_diagnostics(traces_json, project_id)
    else:
        return await _run_simulated_diagnostics(traces_json, project_id)
