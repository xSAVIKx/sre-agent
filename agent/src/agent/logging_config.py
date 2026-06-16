"""GCP Structured Logging configuration for the SRE Agent.

This module provides a custom JSON formatter that maps Python log records
to Google Cloud Logging's structured JSON payload, enabling log-trace correlation,
proper severity level mapping, and custom label mapping.
"""

import os
import json
import logging
import datetime
import sys
from typing import Any

# Try importing opentelemetry trace
try:
    from opentelemetry import trace
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


class StructuredGcpLoggingFormatter(logging.Formatter):
    """A logging Formatter that outputs logs in GCP structured JSON format.

    Automatically extracts OpenTelemetry trace and span contexts for trace-log correlation.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Cache GCP project ID for trace format
        self.project_id = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project"

    def format(self, record: logging.LogRecord) -> str:
        # 1. Extract trace and span context if OTEL is loaded
        trace_id = None
        span_id = None
        trace_sampled = None

        if HAS_OTEL:
            try:
                span = trace.get_current_span()
                if span:
                    context = span.get_span_context()
                    if context and context.is_valid:
                        trace_id = f"{context.trace_id:032x}"
                        span_id = f"{context.span_id:016x}"
                        trace_sampled = bool(context.trace_flags.sampled)
            except Exception:
                # Silently ignore OTEL retrieval exceptions to keep logging robust
                pass

        # 2. Map Python log levels to GCP severity levels
        # Python levels: DEBUG=10, INFO=20, WARNING=30, ERROR=40, CRITICAL=50
        # GCP levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
        severity = record.levelname
        if record.levelno <= logging.DEBUG:
            severity = "DEBUG"
        elif record.levelno <= logging.INFO:
            severity = "INFO"
        elif record.levelno <= logging.WARNING:
            severity = "WARNING"
        elif record.levelno <= logging.ERROR:
            severity = "ERROR"
        else:
            severity = "CRITICAL"

        # 3. Construct the base GCP structured log entry
        log_entry: dict[str, Any] = {
            "timestamp": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat() + "Z",
            "severity": severity,
            "message": record.getMessage(),
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": str(record.lineno),
                "function": record.funcName,
            }
        }

        # 4. Handle exceptions
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_entry["exception"] = record.exc_text

        # 5. Add trace and span correlation fields if present
        if trace_id:
            log_entry["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{trace_id}"
            log_entry["logging.googleapis.com/spanId"] = span_id
            if trace_sampled is not None:
                log_entry["logging.googleapis.com/trace_sampled"] = trace_sampled
            # Also keep traceId at root for general compatibility
            log_entry["traceId"] = trace_id

        # 6. Extract extra fields and map them to GCP labels or root attributes
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message"
        }

        labels: dict[str, str] = {}
        # Keep track of logger name as a label
        labels["logger"] = record.name

        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                # Check for GCP-specific top-level properties
                if key in ("httpRequest", "logging.googleapis.com/operation"):
                    log_entry[key] = value
                elif key == "labels" and isinstance(value, dict):
                    # Direct labels dict provided in extra
                    for l_key, l_val in value.items():
                        labels[str(l_key)] = str(l_val)
                else:
                    # Put all other scalar extra variables in GCP labels and root
                    if isinstance(value, (dict, list)):
                        log_entry[key] = value
                    else:
                        labels[key] = str(value)
                        log_entry[key] = value

        if labels:
            log_entry["logging.googleapis.com/labels"] = labels

        return json.dumps(log_entry)


def setup_logging(level: int = logging.INFO) -> None:
    """Configures application-wide logging.

    Uses StructuredGcpLoggingFormatter for JSON logs if deployed to the cloud
    (or if LOG_FORMAT=json), and a readable console format otherwise.
    """
    is_cloud = os.environ.get("K_SERVICE") is not None
    log_format = os.environ.get("LOG_FORMAT", "json" if is_cloud else "text").lower()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clean existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Setup the stream handler
    handler = logging.StreamHandler(sys.stdout)

    if log_format == "json":
        handler.setFormatter(StructuredGcpLoggingFormatter())
    else:
        # Readable format for local development
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)

    root_logger.addHandler(handler)

    # Configure third-party loggers (like uvicorn, fastapi) to propagate logs
    # so they are also formatted using our StructuredGcpLoggingFormatter
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(logger_name)
        # Clear specific handlers and let logs propagate to the root logger
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(level)
