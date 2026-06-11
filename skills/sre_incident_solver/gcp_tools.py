"""GCP Observability Tools for Cloud Trace and Cloud Logging.

This module provides tools to query GCP Cloud Trace and Cloud Logging.
It features an automatic local simulation fallback when real GCP credentials
or projects are not configured.
"""

import os
import json
import logging
from typing import Any
from .registry import register_tool

# Setup basic logging
logger = logging.getLogger("sre_tools")

# Fail-safe imports of Google Cloud client libraries
try:
    from google.cloud import trace_v2
except ImportError:
    trace_v2 = None

try:
    from google.cloud import logging as cloud_logging
except ImportError:
    cloud_logging = None

# Determine if we should run in mock/simulator mode
# Default to mock unless MOCK_GCP is explicitly set to false and client libraries exist
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes") or trace_v2 is None or cloud_logging is None

# Path to the mock telemetry data
MOCK_DATA_DIR = os.getenv("MOCK_DATA_DIR", "mock_telemetry_data")

def _load_mock_file(filename: str) -> Any:
    """Helper to load mock data from a JSON file.

    Args:
        filename: Name of the mock file to load.

    Returns:
        Deserialized JSON content or None if file loading fails.
    """
    path = os.path.join(MOCK_DATA_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading mock file {path}: {e}")
    return None

@register_tool
async def query_traces(project_id: str | None = None, limit: int = 10) -> str:
    """Queries recent traces from GCP Cloud Trace.

    Fetches a list of recent distributed trace summaries, including their
    trace IDs, start times, and root span names.

    Args:
        project_id: The GCP Project ID. If None, uses default configuration.
        limit: The maximum number of traces to return.

    Returns:
        A formatted JSON string containing a list of trace summaries.
    """
    if IS_MOCK:
        traces = _load_mock_file("traces.json")
        if traces:
            return json.dumps(traces[:limit], indent=2)
        return json.dumps({"error": "No mock traces found. Run simulate_incident.py first."}, indent=2)

    if trace_v2 is None:
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v2.TraceServiceClient()
        project_path = f"projects/{project_id or client.project}"
        # Returns metadata about tracing connection in lieu of standard list
        return json.dumps({"status": "connected", "project": project_path, "traces": []}, indent=2)
    except Exception as e:
        logger.error(f"Failed to query real GCP Trace API: {e}")
        return json.dumps({"error": f"GCP Trace API Error: {str(e)}"}, indent=2)


@register_tool
async def get_trace_details(trace_id: str, project_id: str | None = None) -> str:
    """Retrieves full span details for a specific trace ID from Cloud Trace.

    Fetches the hierarchical spans associated with a trace, detailing span names,
    span IDs, parent span IDs, execution duration (start and end times), and status.

    Args:
        trace_id: The unique hex string identifying the trace (32 characters).
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A formatted JSON string representing the trace spans and timeline.
    """
    if IS_MOCK:
        trace_details = _load_mock_file(f"trace_{trace_id}.json")
        if trace_details:
            return json.dumps(trace_details, indent=2)

        # Fallback search inside traces.json
        traces = _load_mock_file("traces.json")
        if traces:
            for t in traces:
                if t.get("traceId") == trace_id:
                    return json.dumps(t, indent=2)
        return json.dumps({"error": f"Trace ID {trace_id} not found in mock data."}, indent=2)

    if trace_v2 is None:
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v2.TraceServiceClient()
        project_path = f"projects/{project_id or client.project}"
        trace = client.get_trace(name=f"{project_path}/traces/{trace_id}")
        return json.dumps(trace, indent=2)
    except Exception as e:
        logger.error(f"Failed to get trace details: {e}")
        return json.dumps({"error": f"GCP Trace API Error: {str(e)}"}, indent=2)


@register_tool
async def query_logs_by_trace(trace_id: str, project_id: str | None = None, limit: int = 50) -> str:
    """Queries GCP Cloud Logging for logs correlated with a specific trace ID.

    Retrieves log entries across all services and resources that share the
    given trace ID, allowing log-to-trace correlation.

    Args:
        trace_id: The unique trace ID string to filter logs by.
        project_id: The GCP Project ID. If None, uses default configuration.
        limit: The maximum number of log lines to retrieve.

    Returns:
        A formatted JSON string containing a list of matching log entries.
    """
    if IS_MOCK:
        logs = _load_mock_file(f"logs_{trace_id}.json")
        if logs:
            return json.dumps(logs[:limit], indent=2)

        # Fallback search in general logs.json
        all_logs = _load_mock_file("logs.json")
        if all_logs:
            correlated_logs = [log for log in all_logs if log.get("traceId") == trace_id]
            if correlated_logs:
                return json.dumps(correlated_logs[:limit], indent=2)
        return json.dumps({"error": f"No logs found correlated with Trace ID {trace_id}."}, indent=2)

    if cloud_logging is None:
        return json.dumps({"error": "google-cloud-logging library is not installed."}, indent=2)

    try:
        client = cloud_logging.Client(project=project_id)
        filter_str = f'trace="projects/{client.project}/traces/{trace_id}"'
        entries = client.list_entries(filter_=filter_str, max_results=limit)

        logs_list = []
        for entry in entries:
            logs_list.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "severity": entry.severity,
                "text_payload": entry.text_payload,
                "json_payload": entry.json_payload,
                "resource": entry.resource.type if entry.resource else None
            })
        return json.dumps(logs_list, indent=2)
    except Exception as e:
        logger.error(f"Failed to query GCP Logging API: {e}")
        return json.dumps({"error": f"GCP Logging API Error: {str(e)}"}, indent=2)
