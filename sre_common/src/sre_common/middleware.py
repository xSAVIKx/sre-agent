"""Common Middlewares and Request contexts for SRE agents.

Handles W3C trace context extraction, task-local project context isolation,
and contextvar storage for HTTP request lifetimes.
"""

import contextvars
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from sre_common.logging import request_trace_id, request_span_id

logger = logging.getLogger("sre_common.middleware")

# Task-local ContextVar to prevent concurrency race conditions on active target project ID
target_project_contextvar: contextvars.ContextVar[str | None] = contextvars.ContextVar("target_project", default=None)


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Starlette middleware to extract trace/span IDs from request headers
    and store them in context variables for logging correlation.
    """
    async def dispatch(self, request: Request, call_next):
        trace_id = None
        span_id = None

        # 1. GCP X-Cloud-Trace-Context header
        # Format: TRACE_ID/SPAN_ID;o=TRACE_TRUE
        gcp_trace = request.headers.get("x-cloud-trace-context")
        if gcp_trace:
            try:
                parts = gcp_trace.split(";")
                trace_span = parts[0].split("/")
                if len(trace_span) >= 1:
                    trace_id = trace_span[0]
                if len(trace_span) >= 2:
                    span_id = trace_span[1]
            except Exception:
                pass

        # 2. W3C traceparent header
        # Format: 00-trace_id-span_id-flags
        if not trace_id:
            traceparent = request.headers.get("traceparent")
            if traceparent:
                try:
                    parts = traceparent.split("-")
                    if len(parts) >= 3:
                        trace_id = parts[1]
                        span_id = parts[2]
                except Exception:
                    pass

        # Set context variables for the current request task context
        t_token = request_trace_id.set(trace_id)
        s_token = request_span_id.set(span_id)

        try:
            response = await call_next(request)
            return response
        finally:
            # Clean up contextvars
            request_trace_id.reset(t_token)
            request_span_id.reset(s_token)
