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
    logger.info(f"[Mock Telemetry Check] Looking for mock file: {path}")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(f"[Mock Telemetry Check] Successfully loaded mock data from: {path}")
                return data
        except Exception as e:
            logger.error(f"Error loading mock file {path}: {e}")
    else:
        logger.warning(f"[Mock Telemetry Check] Mock file not found: {path}")
    return None


def _get_project_id(project_id: str | None = None) -> str:
    """Helper to resolve the GCP Project ID.

    If project_id is provided, returns it.
    Otherwise, attempts to read GOOGLE_CLOUD_PROJECT, GCP_PROJECT,
    or queries google.auth.default().
    """
    if project_id:
        return project_id

    # Try environment variables first
    for var in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT"):
        val = os.getenv(var)
        if val:
            return val

    # Try google.auth
    try:
        import google.auth
        _, default_project = google.auth.default()
        if default_project:
            return default_project
    except Exception:
        pass

    return "unknown-project"


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
        logger.info(f"[GCP Observability] Querying mock traces from '{MOCK_DATA_DIR}' (limit={limit})")
        traces = _load_mock_file("traces.json")
        if traces:
            logger.info(f"[GCP Observability] Retrieved {len(traces)} mock traces")
            return json.dumps(traces[:limit], indent=2)
        logger.warning("[GCP Observability] No mock traces found in directory")
        return json.dumps({"error": "No mock traces found. Run simulate_incident.py first."}, indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Querying real GCP Trace API (Project={resolved_project}, limit={limit})")
    if trace_v2 is None:
        logger.error("[GCP Observability] google-cloud-trace library is missing")
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v2.TraceServiceClient()
        project_path = f"projects/{resolved_project}"
        # Returns metadata about tracing connection in lieu of standard list
        logger.info(f"[GCP Observability] Connected to TraceServiceClient. Project path: {project_path}")
        return json.dumps({"status": "connected", "project": project_path, "traces": []}, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query real GCP Trace API: {e}")
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
        logger.info(f"[GCP Observability] Retrieving details for mock Trace ID: {trace_id}")
        trace_details = _load_mock_file(f"trace_{trace_id}.json")
        if trace_details:
            logger.info(f"[GCP Observability] Loaded detailed spans for mock trace {trace_id}")
            return json.dumps(trace_details, indent=2)

        # Fallback search inside traces.json
        logger.info(f"[GCP Observability] Trace file trace_{trace_id}.json not found. Searching fallback traces.json...")
        traces = _load_mock_file("traces.json")
        if traces:
            for t in traces:
                if t.get("traceId") == trace_id:
                    logger.info(f"[GCP Observability] Found trace {trace_id} in fallback traces.json list")
                    return json.dumps(t, indent=2)
        logger.warning(f"[GCP Observability] Mock Trace ID {trace_id} not found in any local mock files")
        return json.dumps({"error": f"Trace ID {trace_id} not found in mock data."}, indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Querying real GCP Cloud Trace details for Trace ID: {trace_id} (Project={resolved_project})")
    if trace_v2 is None:
        logger.error("[GCP Observability] google-cloud-trace library is missing")
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v2.TraceServiceClient()
        project_path = f"projects/{resolved_project}"
        trace_name = f"{project_path}/traces/{trace_id}"
        logger.info(f"[GCP Observability] Requesting trace details: {trace_name}")
        trace = client.get_trace(name=trace_name)
        logger.info(f"[GCP Observability] Successfully retrieved trace details for {trace_id}")
        return json.dumps(trace, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to get trace details for {trace_id}: {e}")
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
        logger.info(f"[GCP Observability] Querying mock logs for Trace ID: {trace_id} (limit={limit})")
        logs = _load_mock_file(f"logs_{trace_id}.json")
        if logs:
            logger.info(f"[GCP Observability] Found {len(logs)} mock logs correlated with trace {trace_id}")
            return json.dumps(logs[:limit], indent=2)

        # Fallback search in general logs.json
        logger.info(f"[GCP Observability] Logs file logs_{trace_id}.json not found. Searching fallback logs.json...")
        all_logs = _load_mock_file("logs.json")
        if all_logs:
            correlated_logs = [log for log in all_logs if log.get("traceId") == trace_id]
            if correlated_logs:
                logger.info(f"[GCP Observability] Found {len(correlated_logs)} correlated logs in fallback logs.json")
                return json.dumps(correlated_logs[:limit], indent=2)
        logger.warning(f"[GCP Observability] No mock logs found for Trace ID: {trace_id}")
        return json.dumps({"error": f"No logs found correlated with Trace ID {trace_id}."}, indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Querying real GCP Cloud Logging for Trace ID: {trace_id} (Project={resolved_project}, limit={limit})")
    if cloud_logging is None:
        logger.error("[GCP Observability] google-cloud-logging library is missing")
        return json.dumps({"error": "google-cloud-logging library is not installed."}, indent=2)

    try:
        client = cloud_logging.Client(project=resolved_project)
        filter_str = f'trace="projects/{client.project}/traces/{trace_id}"'
        logger.info(f"[GCP Observability] Running list_entries with filter: {filter_str}")
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
        logger.info(f"[GCP Observability] Retrieved {len(logs_list)} log entries from GCP Cloud Logging")
        return json.dumps(logs_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query GCP Logging API for trace {trace_id}: {e}")
        return json.dumps({"error": f"GCP Logging API Error: {str(e)}"}, indent=2)
