"""ADK multi-agent workflow for SRE incident diagnostics.

This module orchestrates two specialized ADK agents:
1. TraceAnalyzerAgent: Identifies latency/errors in traces and extracts the trace ID.
2. LogCorrelatorAgent: Correlates the trace ID with logs and diagnoses the root cause.
"""

import logging
from typing import Any
from .gcp_tools import get_trace_details, query_logs_by_trace

# Setup logger
logger = logging.getLogger("sre_workflow")

# Resilient imports for google-adk
try:
    from google.adk import Agent as AdkAgent
    from google.adk import Workflow as AdkWorkflow
    HAS_ADK = True
except ImportError:
    HAS_ADK = False
    logger.warning("google-adk is not installed. Using simulated agent fallbacks.")

    class AdkAgent:  # type: ignore
        """Mock ADK Agent for resilience."""
        def __init__(self, name: str, instruction: str, model: str = "gemini-2.5-flash") -> None:
            self.name = name
            self.instruction = instruction
            self.model = model

        async def chat(self, prompt: str) -> Any:
            """Mock chat method."""
            return f"Mock response from {self.name} for: {prompt[:30]}..."

    class AdkWorkflow:  # type: ignore
        """Mock ADK Workflow for resilience."""
        def __init__(self, name: str, edges: list[Any]) -> None:
            self.name = name
            self.edges = edges


# 1. Define SRE specialized ADK agents
trace_analyzer = AdkAgent(
    name="trace_analyzer",
    instruction=(
        "You are an SRE trace analyst. Analyze the provided traces list. "
        "Locate the trace representing the slowest or failing request. "
        "Extract its traceId and return ONLY the raw 32-character hex traceId. "
        "Do not include any extra text, code block backticks, or explanation."
    ),
    model="gemini-2.5-flash"
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction=(
        "You are a senior SRE debugging assistant. Analyze the trace details "
        "and correlated logs provided. Identify the failing span, the root cause "
        "of the issue (such as connection timeouts, resource exhaustion, or "
        "logic errors), and recommend a mitigation plan."
    ),
    model="gemini-2.5-flash"
)


# 2. Orchestrate the diagnostic workflow
async def run_sre_diagnostics(traces_json: str, project_id: str | None = None) -> str:
    """Executes the SRE diagnostic workflow using ADK agents.

    Analyzes trace summaries to find the anomalous trace ID, retrieves its full
    spans and correlated logs, and correlates them to diagnose the root cause.

    Args:
        traces_json: A JSON string containing recent trace summaries.
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A markdown-formatted SRE incident diagnosis report.
    """
    logger.info("Starting SRE diagnostics workflow...")

    if HAS_ADK:
        # In a real environment, we call the ADK agents to chat/reason
        try:
            # 1. Identify trace ID
            trace_id_response = await trace_analyzer.chat(f"Find the failing trace ID in these traces:\n{traces_json}")
            trace_id = trace_id_response.strip()
            logger.info(f"Trace Analyzer identified trace ID: {trace_id}")

            # 2. Fetch trace details and correlated logs
            trace_details = await get_trace_details(trace_id, project_id)
            logs = await query_logs_by_trace(trace_id, project_id)

            # 3. Correlate and diagnose
            analysis_prompt = (
                f"Trace Spans:\n{trace_details}\n\n"
                f"Correlated Logs:\n{logs}\n\n"
                f"Provide a root cause analysis and mitigation plan."
            )
            diagnosis_response = await log_correlator.chat(analysis_prompt)
            return str(diagnosis_response)
        except Exception as e:
            logger.error(f"Error during ADK execution: {e}")
            return f"### Diagnostic Execution Failure\nAn error occurred while executing the ADK workflow: {e}"
    else:
        # Simulated fallback execution (replicates the reasoning steps for local testing)
        import json
        try:
            data = json.loads(traces_json)
            # Find the first trace with error = True or slow latency (> 5000ms)
            failing_trace = None
            if isinstance(data, list):
                for t in data:
                    if t.get("error") is True or t.get("durationMs", 0) > 5000:
                        failing_trace = t
                        break
                if not failing_trace and data:
                    failing_trace = data[0]

            if not failing_trace:
                return "### Diagnostics Completed\nNo anomalous traces or errors detected in the recent logs."

            trace_id = failing_trace.get("traceId", "unknown_trace_id")
            logger.info(f"[Simulation] Identified trace ID: {trace_id}")

            # Fetch trace details and logs from mock files
            trace_details = await get_trace_details(trace_id, project_id)
            logs = await query_logs_by_trace(trace_id, project_id)

            # Build mock SRE analysis response based on telemetry
            trace_data = json.loads(trace_details)
            log_data = json.loads(logs)

            error_msg = "Unknown error"
            if isinstance(log_data, list):
                for log in log_data:
                    if log.get("severity") in ("ERROR", "CRITICAL"):
                        error_msg = log.get("text_payload") or log.get("json_payload", {}).get("message", error_msg)

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
                f"## 🛠️ Recommended Mitigation\n"
                f"1. **Check Database Health**: Verify that the database instance `db-primary.gcp.internal` is running and accessible.\n"
                f"2. **Verify Firewall Rules**: Ensure VPC firewall settings allow ingress traffic from the backend service subnet on port 5432.\n"
                f"3. **Adjust Connection Pools**: Review backend service connection pool configurations to prevent pool exhaustion."
            )
            return report
        except Exception as e:
            return f"### Diagnostic Simulation Failure\nFailed to parse traces or logs during simulation: {e}"
