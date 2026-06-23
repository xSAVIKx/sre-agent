"""GCP Observability Tools for Cloud Trace and Cloud Logging.

This module provides tools to query GCP Cloud Trace and Cloud Logging.
It features an automatic local simulation fallback when real GCP credentials
or projects are not configured.
"""

import os
import json
import logging
import datetime
from typing import Any
from sre_agent.registry import register_tool
import contextlib
from sre_common import retry_async, otel_trace, start_span

# Setup basic logging
logger = logging.getLogger("sre_tools")



# Fail-safe imports of Google Cloud client libraries
try:
    from google.cloud import trace_v1
except ImportError:
    trace_v1 = None

try:
    from google.cloud import logging as cloud_logging
except ImportError:
    cloud_logging = None

try:
    from google.cloud import monitoring_v3
except ImportError:
    monitoring_v3 = None

# Determine if we should run in mock/simulator mode
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes") or trace_v1 is None or cloud_logging is None or monitoring_v3 is None

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
    Otherwise, attempts to check target_project_contextvar, read GOOGLE_CLOUD_PROJECT,
    GCP_PROJECT, or queries google.auth.default().
    """
    if project_id:
        return project_id

    # Check the ContextVar to prevent concurrency race conditions
    try:
        from sre_common.middleware import target_project_contextvar
        ctx_val = target_project_contextvar.get()
        if ctx_val:
            return ctx_val
    except Exception:
        pass

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


def _parse_timestamp(ts_str: str) -> datetime.datetime | None:
    """Parses an RFC3339 timestamp string into a datetime object.

    Handles optional fractional seconds.

    Args:
        ts_str: The timestamp string to parse.

    Returns:
        A datetime object if parsing was successful, otherwise None.
    """
    if not ts_str:
        return None
    try:
        ts_str = ts_str.rstrip('Z')
        if '.' in ts_str:
            base, frac = ts_str.split('.')
            frac = frac[:6]
            ts_str = f"{base}.{frac}"
            return datetime.datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S.%f')
        return datetime.datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S')
    except Exception as e:
        logger.warning(f"Failed to parse timestamp string '{ts_str}': {e}")
        return None


def _calculate_duration_ms(start_time_str: str, end_time_str: str) -> int:
    """Calculates duration in milliseconds between two timestamp strings.

    Args:
        start_time_str: Start time timestamp string.
        end_time_str: End time timestamp string.

    Returns:
        Duration in milliseconds, or 0 if parsing or calculation fails.
    """
    st = _parse_timestamp(start_time_str)
    et = _parse_timestamp(end_time_str)
    if st and et:
        try:
            return int((et - st).total_seconds() * 1000)
        except Exception as e:
            logger.warning(f"Failed to calculate duration: {e}")
    return 0


def _find_root_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Finds the root span from a list of trace spans.

    The root span is identified as having no parent_span_id or a parent_span_id of "0".
    If no root span matches, the first span in the list is returned as fallback.

    Args:
        spans: List of span dictionaries.

    Returns:
        The root span dictionary, or None if the list is empty.
    """
    for span in spans:
        p_id = span.get("parent_span_id", "0")
        if p_id == "0" or p_id == "" or p_id is None:
            return span
    if spans:
        return spans[0]
    return None


def _check_span_error(span: dict[str, Any]) -> bool:
    """Checks if a span contains error indicators in its labels/attributes.

    Args:
        span: Span dictionary.

    Returns:
        True if the span has errors, False otherwise.
    """
    labels = span.get("labels", {})
    status_code = labels.get("/http/status_code", "")
    if status_code.startswith("5"):
        return True
    for key in labels:
        if "error" in key.lower():
            return True
    return False


def _check_trace_error(spans: list[dict[str, Any]]) -> bool:
    """Checks if any span in the trace contains an error.

    Args:
        spans: List of span dictionaries.

    Returns:
        True if any span has an error, False otherwise.
    """
    for span in spans:
        if _check_span_error(span):
            return True
    return False


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("query_traces")
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
    if trace_v1 is None:
        logger.error("[GCP Observability] google-cloud-trace library is missing")
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v1.TraceServiceClient()
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(hours=2)
        req = trace_v1.ListTracesRequest(
            project_id=resolved_project,
            start_time=start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            view=trace_v1.ListTracesRequest.ViewType.COMPLETE
        )
        pager = client.list_traces(request=req)
        
        traces_list = []
        for trace_item in pager:
            if len(traces_list) >= 100:
                break
            trace_dict = trace_v1.Trace.to_dict(trace_item)
            t_id = trace_dict.get("trace_id", "")
            spans = trace_dict.get("spans", [])
            
            root_span = _find_root_span(spans)
            start_time_str = ""
            duration_ms = 0
            root_name = "unknown"
            
            if root_span:
                root_name = root_span.get("name", "unknown")
                start_time_str = root_span.get("start_time", "")
                end_time_str = root_span.get("end_time", "")
                duration_ms = _calculate_duration_ms(start_time_str, end_time_str)
                        
            has_error = _check_trace_error(spans)
                    
            traces_list.append({
                "traceId": t_id,
                "name": root_name,
                "startTime": start_time_str,
                "durationMs": duration_ms,
                "error": has_error
            })
            
        # Sort traces by startTime descending (newest first)
        traces_list.sort(key=lambda x: x["startTime"], reverse=True)
        traces_list = traces_list[:limit]
            
        logger.info(f"[GCP Observability] Successfully queried {len(traces_list)} traces from real GCP Trace API")
        return json.dumps(traces_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query real GCP Trace API: {e}")
        return json.dumps({"error": f"GCP Trace API Error: {str(e)}"}, indent=2)


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("get_trace_details")
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
    if trace_v1 is None:
        logger.error("[GCP Observability] google-cloud-trace library is missing")
        return json.dumps({"error": "google-cloud-trace library is not installed."}, indent=2)

    try:
        client = trace_v1.TraceServiceClient()
        trace_obj = client.get_trace(project_id=resolved_project, trace_id=trace_id)
        trace_dict = trace_v1.Trace.to_dict(trace_obj)
        
        spans_list = []
        duration_ms = 0
        root_name = "unknown"
        
        spans = trace_dict.get("spans", [])
        root_span = _find_root_span(spans)
            
        if root_span:
            root_name = root_span.get("name", "unknown")
            start_time_str = root_span.get("start_time", "")
            end_time_str = root_span.get("end_time", "")
            duration_ms = _calculate_duration_ms(start_time_str, end_time_str)
                    
        has_error = _check_trace_error(spans)

        for span in spans:
            span_error = _check_span_error(span)
            labels = span.get("labels", {})
            error_message = labels.get("/error/message") or labels.get("error_message") or labels.get("/error/name")
            p_id = span.get("parent_span_id", "0")
            parent_span_id = None if (p_id == "0" or p_id == "" or p_id is None) else p_id
            
            spans_list.append({
                "name": span.get("name", ""),
                "spanId": span.get("span_id", ""),
                "parentSpanId": parent_span_id,
                "startTime": span.get("start_time", ""),
                "endTime": span.get("end_time", ""),
                "status": "ERROR" if span_error else "OK",
                "error_message": error_message
            })
            
        result = {
            "traceId": trace_id,
            "root_span": root_name,
            "durationMs": duration_ms,
            "error": has_error,
            "spans": spans_list
        }
        logger.info(f"[GCP Observability] Successfully formatted trace details for {trace_id}")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to get trace details for {trace_id}: {e}")
        return json.dumps({"error": f"GCP Trace API Error: {str(e)}"}, indent=2)


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("query_logs_by_trace")
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
        if not logs:
            # Fallback search in general logs.json
            logger.info(f"[GCP Observability] Logs file logs_{trace_id}.json not found. Searching fallback logs.json...")
            all_logs = _load_mock_file("logs.json")
            if all_logs:
                logs = [log for log in all_logs if log.get("traceId") == trace_id]

        if logs:
            logger.info(f"[GCP Observability] Found {len(logs)} mock logs correlated with trace {trace_id}")
            formatted_logs = []
            for log in logs[:limit]:
                msg = log.get("message", "")
                is_json = False
                try:
                    if isinstance(msg, dict):
                        is_json = True
                    elif isinstance(msg, str) and (msg.startswith("{") or msg.startswith("[")):
                        msg = json.loads(msg)
                        is_json = True
                except Exception:
                    pass
                
                formatted_logs.append({
                    "timestamp": log.get("timestamp"),
                    "severity": log.get("severity"),
                    "text_payload": msg if not is_json else None,
                    "json_payload": msg if is_json else None,
                    "resource": "cloud_run_revision"
                })
            return json.dumps(formatted_logs, indent=2)

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
            is_json = isinstance(entry.payload, dict)
            logs_list.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "severity": entry.severity,
                "text_payload": entry.payload if not is_json else None,
                "json_payload": entry.payload if is_json else None,
                "resource": entry.resource.type if entry.resource else None
            })
        logger.info(f"[GCP Observability] Retrieved {len(logs_list)} log entries from GCP Cloud Logging")
        return json.dumps(logs_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query GCP Logging API for trace {trace_id}: {e}")
        return json.dumps({"error": f"GCP Logging API Error: {str(e)}"}, indent=2)


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("query_logs")
async def query_logs(query: str, project_id: str | None = None, limit: int = 50) -> str:
    """Queries GCP Cloud Logging with a custom filter query.

    Retrieves log entries across services, allowing flexible filtering using
    GCP Cloud Logging syntax.

    Args:
        query: The Cloud Logging filter expression (e.g. 'severity=ERROR' or service indicators).
        project_id: The GCP Project ID. If None, uses default configuration.
        limit: The maximum number of log entries to retrieve.

    Returns:
        A formatted JSON string containing a list of matching log entries.
    """
    if IS_MOCK:
        if any(x in query.lower() for x in ("sre-agent", "sre_agent", "self")):
            logger.info("[GCP Observability] Performing self-diagnostic log review in mock mode.")
            mock_agent_logs = [
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "INFO",
                    "message": "Antigravity SRE Agent version 0.1.0 starting up...",
                },
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "INFO",
                    "message": "Successfully registered safety policy: denyAllExceptObservability",
                },
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "INFO",
                    "message": "Successfully connected to Firestore default database (emulator).",
                },
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "WARNING",
                    "message": "SRE Agent database fetch timeout when reading session metadata (1500ms). Retrying...",
                },
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "ERROR",
                    "message": "FirestoreStrategyException: Failed to update document session_9999 - write transaction aborted.",
                },
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "severity": "INFO",
                    "message": "Retrying Firestore update for session_9999: attempt 2 succeeded.",
                }
            ]
            return json.dumps(mock_agent_logs, indent=2)

        logger.info(f"[GCP Observability] Querying mock logs for filter '{query}' (limit={limit})")
        logs = _load_mock_file("logs.json") or []
        filtered_logs = []
        
        clean_query = query.lower()
        for term in ("and", "or", "resource.type", "severity", "resource.labels.service_name", "=", "\"", "'"):
            clean_query = clean_query.replace(term, " ")
        keywords = [k.strip() for k in clean_query.split() if k.strip()]
        
        severity_filter = None
        for sev in ("error", "critical", "warning", "info", "debug"):
            if sev in query.lower():
                severity_filter = sev.upper()
                break

        for log in logs:
            msg = log.get("message", "")
            msg_str = str(msg).lower()
            sev_str = str(log.get("severity", "")).upper()
            
            if severity_filter and severity_filter != sev_str:
                continue
                
            if keywords:
                matches_keywords = True
                for kw in keywords:
                    if kw not in msg_str and kw not in sev_str.lower():
                        matches_keywords = False
                        break
                if not matches_keywords:
                    continue
            
            is_json = isinstance(msg, dict)
            try:
                if isinstance(msg, str) and (msg.startswith("{") or msg.startswith("[")):
                    msg = json.loads(msg)
                    is_json = True
            except Exception:
                pass
                
            filtered_logs.append({
                "timestamp": log.get("timestamp"),
                "severity": log.get("severity"),
                "text_payload": msg if not is_json else None,
                "json_payload": msg if is_json else None,
                "resource": "cloud_run_revision"
            })
            
        logger.info(f"[GCP Observability] Filtered {len(filtered_logs)} mock logs for query '{query}'")
        return json.dumps(filtered_logs[:limit], indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Querying real GCP Cloud Logging with query: {query} (Project={resolved_project}, limit={limit})")
    if cloud_logging is None:
        logger.error("[GCP Observability] google-cloud-logging library is missing")
        return json.dumps({"error": "google-cloud-logging library is not installed."}, indent=2)

    try:
        client = cloud_logging.Client(project=resolved_project)
        logger.info(f"[GCP Observability] Running list_entries with query filter: {query}")
        entries = client.list_entries(filter_=query, max_results=limit)

        logs_list = []
        for entry in entries:
            is_json = isinstance(entry.payload, dict)
            logs_list.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "severity": entry.severity,
                "text_payload": entry.payload if not is_json else None,
                "json_payload": entry.payload if is_json else None,
                "resource": entry.resource.type if entry.resource else None
            })
        logger.info(f"[GCP Observability] Retrieved {len(logs_list)} log entries from GCP Cloud Logging")
        return json.dumps(logs_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query GCP Logging API with query '{query}': {e}")
        return json.dumps({"error": f"GCP Logging API Error: {str(e)}"}, indent=2)


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("query_metrics")
async def query_metrics(
    filter_expression: str,
    duration_minutes: int = 15,
    project_id: str | None = None
) -> str:
    """Queries GCP Cloud Monitoring for time series metrics.

    Fetches timeseries data points for the given filter expression and duration.

    Args:
        filter_expression: The Monitoring filter expression (e.g. 'metric.type="run.googleapis.com/container/cpu/utilizations"').
        duration_minutes: The history window duration in minutes to retrieve.
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A formatted JSON string representing the retrieved time series points.
    """
    if IS_MOCK:
        logger.info(f"[GCP Observability] Querying mock metrics for filter '{filter_expression}' (duration={duration_minutes}m)")
        metrics = _load_mock_file("metrics.json") or []
        
        # Simple mock filtering: match filter expression containing service name or metric type
        filtered_metrics = []
        filter_clean = filter_expression.lower().replace('"', '').replace("'", "")
        for ts in metrics:
            metric_type = ts.get("metric", {}).get("type", "").lower()
            service_name = ts.get("metric", {}).get("labels", {}).get("service_name", "").lower()
            database_id = ts.get("metric", {}).get("labels", {}).get("database_id", "").lower()
            
            # The metric type must match (be present in the filter)
            if metric_type not in filter_clean:
                continue
                
            # If the filter specifies a service_name, it must match this metric's service_name
            if "service_name" in filter_clean:
                if not service_name or service_name not in filter_clean:
                    continue
            
            # If the filter specifies a database_id, it must match this metric's database_id
            if "database_id" in filter_clean:
                if not database_id or database_id not in filter_clean:
                    continue
                    
            filtered_metrics.append(ts)
                
        logger.info(f"[GCP Observability] Found {len(filtered_metrics)} matching mock metrics")
        return json.dumps(filtered_metrics, indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Querying real GCP Monitoring API (Project={resolved_project}, filter={filter_expression})")
    if monitoring_v3 is None:
        logger.error("[GCP Observability] google-cloud-monitoring library is missing")
        return json.dumps({"error": "google-cloud-monitoring library is not installed."}, indent=2)

    try:
        client = monitoring_v3.MetricServiceClient()
        project_name = f"projects/{resolved_project}"
        
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(minutes=duration_minutes)
        
        interval = monitoring_v3.TimeInterval({
            "end_time": {"seconds": int(now.timestamp())},
            "start_time": {"seconds": int(start.timestamp())}
        })
        
        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": filter_expression,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL
            }
        )
        
        time_series_list = []
        for ts in results:
            ts_dict = monitoring_v3.TimeSeries.to_dict(ts)
            ts_dict["metric_kind"] = ts.metric_kind.name if hasattr(ts.metric_kind, 'name') else str(ts.metric_kind)
            ts_dict["value_type"] = ts.value_type.name if hasattr(ts.value_type, 'name') else str(ts.value_type)
            time_series_list.append(ts_dict)
            
        logger.info(f"[GCP Observability] Successfully queried {len(time_series_list)} timeseries from GCP Monitoring")
        return json.dumps(time_series_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to query GCP Monitoring API: {e}")
        return json.dumps({"error": f"GCP Monitoring API Error: {str(e)}"}, indent=2)


@register_tool
@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("list_metric_descriptors")
async def list_metric_descriptors(filter_expression: str | None = None, project_id: str | None = None) -> str:
    """Lists metric descriptors in GCP Cloud Monitoring.

    Fetches descriptors matching the filter to discover available metrics.

    Args:
        filter_expression: Optional filter to restrict returned descriptors (e.g. 'metric.type = starts_with("run.googleapis.com/")').
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A formatted JSON string listing matching metric descriptors.
    """
    if IS_MOCK:
        logger.info(f"[GCP Observability] Listing mock metric descriptors (filter={filter_expression})")
        descriptors = [
            {
                "name": "projects/simulation-project-123/metricDescriptors/run.googleapis.com/container/cpu/utilizations",
                "type": "run.googleapis.com/container/cpu/utilizations",
                "metric_kind": "GAUGE",
                "value_type": "DOUBLE",
                "description": "CPU utilization of the Container",
                "display_name": "Container CPU utilization"
            },
            {
                "name": "projects/simulation-project-123/metricDescriptors/run.googleapis.com/container/memory/utilizations",
                "type": "run.googleapis.com/container/memory/utilizations",
                "metric_kind": "GAUGE",
                "value_type": "DOUBLE",
                "description": "Memory utilization of the Container",
                "display_name": "Container Memory utilization"
            },
            {
                "name": "projects/simulation-project-123/metricDescriptors/cloudsql.googleapis.com/database/postgresql/connection_count",
                "type": "cloudsql.googleapis.com/database/postgresql/connection_count",
                "metric_kind": "GAUGE",
                "value_type": "INT64",
                "description": "Database connection count",
                "display_name": "Database Connection Count"
            }
        ]
        if filter_expression:
            filter_lower = filter_expression.lower()
            descriptors = [d for d in descriptors if d["type"].lower() in filter_lower or filter_lower in d["type"].lower()]
        return json.dumps(descriptors, indent=2)

    resolved_project = _get_project_id(project_id)
    logger.info(f"[GCP Observability] Listing real GCP Metric Descriptors (Project={resolved_project}, filter={filter_expression})")
    if monitoring_v3 is None:
        logger.error("[GCP Observability] google-cloud-monitoring library is missing")
        return json.dumps({"error": "google-cloud-monitoring library is not installed."}, indent=2)

    try:
        client = monitoring_v3.MetricServiceClient()
        project_name = f"projects/{resolved_project}"
        
        results = client.list_metric_descriptors(
            request={
                "name": project_name,
                "filter": filter_expression or ""
            }
        )
        
        descriptors_list = []
        from google.protobuf.json_format import MessageToDict
        for desc in results:
            desc_dict = MessageToDict(desc, preserving_proto_field_name=True)
            if "metric_kind" not in desc_dict:
                desc_dict["metric_kind"] = desc.metric_kind.name if hasattr(desc.metric_kind, 'name') else str(desc.metric_kind)
            if "value_type" not in desc_dict:
                desc_dict["value_type"] = desc.value_type.name if hasattr(desc.value_type, 'name') else str(desc.value_type)
            descriptors_list.append(desc_dict)
            
        logger.info(f"[GCP Observability] Successfully listed {len(descriptors_list)} metric descriptors")
        return json.dumps(descriptors_list, indent=2)
    except Exception as e:
        logger.error(f"[GCP Observability] Failed to list GCP Metric Descriptors: {e}")
        return json.dumps({"error": f"GCP Metric Descriptors Error: {str(e)}"}, indent=2)
