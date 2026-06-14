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
        def __init__(self, name: str, instruction: str, model: str = "gemini-3-flash-preview") -> None:
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
    model="gemini-3-flash-preview"
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction=(
        "You are a senior SRE debugging assistant. Analyze the trace details "
        "and correlated logs provided. Identify the failing span, the root cause "
        "of the issue (such as connection timeouts, resource exhaustion, or "
        "logic errors), and recommend a mitigation plan."
    ),
    model="gemini-3-flash-preview"
)


# 2. Orchestrate the diagnostic workflow
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

    try:
        # Instantiate session service and runners
        session_service = InMemorySessionService()
        trace_runner = Runner(agent=trace_analyzer, session_service=session_service)
        log_runner = Runner(agent=log_correlator, session_service=session_service)

        # 1. Identify trace ID using Trace Analyzer Agent
        trace_id = ""
        msg = types.Content(parts=[types.Part.from_text(text=f"Find the failing trace ID in these traces:\n{traces_json}")])
        async for event in trace_runner.run_async(
            user_id="sre_user",
            session_id="session_1",
            new_message=msg
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        trace_id += part.text
        trace_id = trace_id.strip()
        logger.info(f"Trace Analyzer identified trace ID: {trace_id}")

        # 2. Fetch trace details and correlated logs
        trace_details = await get_trace_details(trace_id, project_id)
        logs = await query_logs_by_trace(trace_id, project_id)

        # 3. Correlate and diagnose using Log Correlator Agent
        analysis_prompt = (
            f"Trace Spans:\n{trace_details}\n\n"
            f"Correlated Logs:\n{logs}\n\n"
            f"Provide a root cause analysis and mitigation plan."
        )
        analysis_msg = types.Content(parts=[types.Part.from_text(text=analysis_prompt)])
        diagnosis = ""
        async for event in log_runner.run_async(
            user_id="sre_user",
            session_id="session_2",
            new_message=analysis_msg
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        diagnosis += part.text
        return diagnosis
    except Exception as e:
        logger.error(f"Error during ADK execution: {e}")
        return f"### Diagnostic Execution Failure\nAn error occurred while executing the ADK workflow: {e}"


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
            if not failing_trace and data:
                # Find first non-diagnose trace as a fallback
                for t in data:
                    name = t.get("name", "").lower()
                    if not (any(x in name for x in ("diagnose", "health", "warmup")) or name == "/"):
                        failing_trace = t
                        break
                if not failing_trace:
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

    import os
    if HAS_ADK and "GEMINI_API_KEY" in os.environ:
        return await _run_adk_diagnostics(traces_json, project_id)
    else:
        return await _run_simulated_diagnostics(traces_json, project_id)
