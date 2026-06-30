"""FastAPI Target Application demonstrating Tracing and Logging.

This application acts as the target stack for the SRE agent. It implements
a three-tier microservice simulation (Gateway -> Backend -> Database) and
supports real GCP Cloud Trace/Logging integration as well as a local mock mode
that writes synthetic telemetry to a local directory for simulation testing.
"""

import os
import time
import uuid
import json
import logging
import httpx
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Request

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("target_app")

# Determine mock status
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")
MOCK_DATA_DIR = os.getenv("MOCK_DATA_DIR", "mock_telemetry_data")

# Fail-safe OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False
    logger.warning("OpenTelemetry libraries not installed. Telemetry will fall back to stdout/simulation.")

# Initialize OpenTelemetry if available and not in mock mode
if HAS_OTEL and not IS_MOCK:
    try:
        provider = TracerProvider()
        # Export traces directly to GCP Cloud Trace
        exporter = CloudTraceSpanExporter()
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(__name__)
    except Exception as e:
        logger.error(f"Failed to initialize real GCP Trace Exporter: {e}")
        IS_MOCK = True
else:
    tracer = None

# Create FastAPI application
app = FastAPI(
    title="SRE Codelab Target Application",
    description="Simulated multi-tier application generating traces and logs.",
    version="0.1.0"
)


def _log_structured(message: str, severity: str, trace_id: str, span_id: str | None = None) -> None:
    """Logs a structured JSON message to stdout.

    Cloud Run automatically parses JSON logs and maps the trace ID to Cloud Logging.
    In mock mode, this also appends to the mock telemetry log file.

    Args:
        message: The log message text.
        severity: The log severity level (e.g. INFO, ERROR).
        trace_id: The 32-character hex trace ID.
        span_id: The 16-character hex span ID.
    """
    log_entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "severity": severity,
        "message": message,
        "logging.googleapis.com/trace": f"projects/{os.getenv('GCP_PROJECT', 'mock-project')}/traces/{trace_id}",
        "logging.googleapis.com/spanId": span_id,
        "traceId": trace_id
    }

    # Log to console
    print(json.dumps(log_entry))

    # In mock mode, save this log entry to the mock telemetry database
    if IS_MOCK:
        os.makedirs(MOCK_DATA_DIR, exist_ok=True)
        # 1. Update logs_traceid.json
        logs_file = os.path.join(MOCK_DATA_DIR, f"logs_{trace_id}.json")
        logs = []
        if os.path.exists(logs_file):
            try:
                with open(logs_file, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                pass
        logs.append(log_entry)
        with open(logs_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)

        # 2. Append to general logs.json
        all_logs_file = os.path.join(MOCK_DATA_DIR, "logs.json")
        all_logs = []
        if os.path.exists(all_logs_file):
            try:
                with open(all_logs_file, "r", encoding="utf-8") as f:
                    all_logs = json.load(f)
            except Exception:
                pass
        all_logs.append(log_entry)
        with open(all_logs_file, "w", encoding="utf-8") as f:
            json.dump(all_logs, f, indent=2)


def _generate_mock_trace(trace_id: str, trigger_error: bool) -> None:
    """Generates and writes a mock trace JSON file for local simulation.

    Args:
        trace_id: The 32-character hex trace ID.
        trigger_error: Whether this trace simulates an error state.
    """
    os.makedirs(MOCK_DATA_DIR, exist_ok=True)
    # Inclusive (wall-clock) duration of each tier in milliseconds. On an injected
    # error the database tier dominates (a ~10s connection timeout); each upstream
    # tier adds a little self-time on top so the cascade has a clear bottleneck.
    db_duration = 10200 if trigger_error else 30
    backend_duration = db_duration + 50
    gateway_duration = backend_duration + 20

    # Start offsets (ms from T0); children start slightly after their parent.
    gw_start, be_start, db_start = 0, 10, 30

    def _ts(ms: int) -> str:
        """Formats an integer millisecond offset as a valid RFC3339 timestamp.

        The value must occupy the seconds + milliseconds fields (e.g. 10270 ms ->
        ``...:10.270Z``). Placing it in the fractional-seconds field instead
        (``...:00.10270Z``) makes ``%f`` parse it as ~102 ms, collapsing the
        cascade duration math.
        """
        secs, millis = divmod(ms, 1000)
        return f"2026-06-11T16:00:{secs:02d}.{millis:03d}Z"

    trace_data = {
        "traceId": trace_id,
        "root_span": "gateway",
        "durationMs": gateway_duration,
        "error": trigger_error,
        "spans": [
            {
                "name": "/api/gateway",
                "spanId": "span-gateway-111",
                "parentSpanId": None,
                "startTime": _ts(gw_start),
                "endTime": _ts(gw_start + gateway_duration),
                "status": "ERROR" if trigger_error else "OK"
            },
            {
                "name": "/api/backend",
                "spanId": "span-backend-222",
                "parentSpanId": "span-gateway-111",
                "startTime": _ts(be_start),
                "endTime": _ts(be_start + backend_duration),
                "status": "ERROR" if trigger_error else "OK"
            },
            {
                "name": "/api/database",
                "spanId": "span-database-333",
                "parentSpanId": "span-backend-222",
                "startTime": _ts(db_start),
                "endTime": _ts(db_start + db_duration),
                "status": "ERROR" if trigger_error else "OK",
                "error_message": "ConnectionTimeoutError: Failed to connect to db-primary.gcp.internal:5432 after 10000ms" if trigger_error else None
            }
        ]
    }

    # Write trace detail file
    with open(os.path.join(MOCK_DATA_DIR, f"trace_{trace_id}.json"), "w", encoding="utf-8") as f:
        json.dump(trace_data, f, indent=2)

    # Append to general traces.json list
    traces_file = os.path.join(MOCK_DATA_DIR, "traces.json")
    traces = []
    if os.path.exists(traces_file):
        try:
            with open(traces_file, "r", encoding="utf-8") as f:
                traces = json.load(f)
        except Exception:
            pass
    # Put new traces at the beginning
    traces.insert(0, {
        "traceId": trace_id,
        "name": "/api/gateway",
        "startTime": "2026-06-11T16:00:00.000Z",
        "durationMs": gateway_duration,
        "error": trigger_error
    })
    with open(traces_file, "w", encoding="utf-8") as f:
        json.dump(traces, f, indent=2)


@app.get("/api/gateway")
async def gateway(request: Request, trigger_error: bool = Query(default=False)) -> dict[str, Any]:
    """API Gateway entrypoint.

    Simulates the gateway layer receiving a client request. It generates a trace ID
    and delegates to the downstream backend service.

    Args:
        trigger_error: If True, forces downstream database error simulation.

    Returns:
        A response dictionary containing status details and the trace ID.
    """
    # 1. Start Trace ID definition
    trace_id = uuid.uuid4().hex
    _log_structured("Gateway received request to /api/gateway", "INFO", trace_id, "span-gateway-111")

    # In mock/simulation mode, we write mock files
    if IS_MOCK:
        _log_structured("Routing request to backend service...", "INFO", trace_id, "span-gateway-111")
        # Simulate backend call delay
        time.sleep(0.05)
        # Call mock backend helper directly
        try:
            backend_response = await backend(request, trace_id, trigger_error)
            _log_structured("Gateway received success response from backend", "INFO", trace_id, "span-gateway-111")
            _generate_mock_trace(trace_id, trigger_error=False)
            return {"status": "success", "trace_id": trace_id, "data": backend_response}
        except HTTPException as e:
            _log_structured(f"Gateway received error from backend: {e.detail}", "ERROR", trace_id, "span-gateway-111")
            _generate_mock_trace(trace_id, trigger_error=True)
            raise HTTPException(status_code=500, detail={"error": "Internal Server Error", "trace_id": trace_id})

    # Real OTEL tracing (if active)
    if tracer:
        with tracer.start_as_current_span("/api/gateway") as span:
            span.set_attribute("http.method", "GET")
            otel_trace_id = f"{span.get_span_context().trace_id:032x}"
            # Call downstream backend service using httpx (injecting trace context headers)
            # In a real deployed setup, the backend URL is fetched from env
            backend_url = os.getenv("BACKEND_SERVICE_URL", "http://localhost:8080")
            async with httpx.AsyncClient() as client:
                headers = {}
                # Inject tracing headers
                headers["traceparent"] = f"00-{otel_trace_id}-{span.get_span_context().span_id:016x}-01"
                try:
                    response = await client.get(f"{backend_url}/api/backend?trace_id={otel_trace_id}&trigger_error={str(trigger_error).lower()}", headers=headers)
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail="Backend failed")
                    return {"status": "success", "trace_id": otel_trace_id, "data": response.json()}
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    raise HTTPException(status_code=500, detail={"error": str(e), "trace_id": otel_trace_id})

    return {"status": "success", "trace_id": trace_id, "info": "OTEL disabled"}


@app.get("/api/backend")
async def backend(request: Request, trace_id: str = Query(...), trigger_error: bool = Query(default=False)) -> dict[str, Any]:
    """Backend service endpoint.

    Delegates the processing logic to the database layer.

    Args:
        trace_id: The correlation trace ID.
        trigger_error: If True, triggers database connection failure.

    Returns:
        A dictionary containing service execution results.
    """
    _log_structured("Backend service processing business logic", "INFO", trace_id, "span-backend-222")

    if IS_MOCK:
        time.sleep(0.02)
        # Delegate to database helper directly
        db_response = await database(request, trace_id, trigger_error)
        _log_structured("Backend service database query completed", "INFO", trace_id, "span-backend-222")
        return {"service": "backend", "db": db_response}

    # Real OTEL tracing (if active)
    if tracer:
        parent_context = TraceContextTextMapPropagator().extract(carrier=request.headers)
        with tracer.start_as_current_span("/api/backend", context=parent_context) as span:
            backend_url = os.getenv("BACKEND_SERVICE_URL", "http://localhost:8080")
            async with httpx.AsyncClient() as client:
                headers = {}
                headers["traceparent"] = f"00-{trace_id}-{span.get_span_context().span_id:016x}-01"
                try:
                    response = await client.get(
                        f"{backend_url}/api/database?trace_id={trace_id}&trigger_error={str(trigger_error).lower()}",
                        headers=headers
                    )
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail="Database failed")
                    return {"service": "backend", "db": response.json()}
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    raise HTTPException(status_code=500, detail={"error": str(e), "trace_id": trace_id})

    # Real mode without tracer active
    backend_url = os.getenv("BACKEND_SERVICE_URL", "http://localhost:8080")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{backend_url}/api/database?trace_id={trace_id}&trigger_error={str(trigger_error).lower()}"
            )
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Database failed")
            return {"service": "backend", "db": response.json()}
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": str(e), "trace_id": trace_id})


@app.get("/api/database")
async def database(request: Request, trace_id: str = Query(...), trigger_error: bool = Query(default=False)) -> dict[str, Any]:
    """Database simulator service.

    Simulates the database query layer.

    Args:
        trace_id: The correlation trace ID.
        trigger_error: If True, forces connection timeout.

    Returns:
        A dictionary containing query execution status.
    """
    _log_structured("Database connecting to instance: db-primary.gcp.internal:5432", "INFO", trace_id, "span-database-333")

    if tracer and not IS_MOCK:
        parent_context = TraceContextTextMapPropagator().extract(carrier=request.headers)
        with tracer.start_as_current_span("/api/database", context=parent_context) as span:
            if trigger_error:
                time.sleep(10.0)
                err_msg = "ConnectionTimeoutError: Failed to connect to db-primary.gcp.internal:5432 after 10000ms"
                _log_structured(err_msg, "CRITICAL", trace_id, "span-database-333")
                span.record_exception(Exception(err_msg))
                span.set_status(trace.StatusCode.ERROR, err_msg)
                raise HTTPException(status_code=500, detail=err_msg)
            return {"service": "database", "query": "SELECT * FROM users LIMIT 1", "rows": 1}
    else:
        if trigger_error:
            # Simulate connection timeout latency
            if IS_MOCK:
                time.sleep(0.1)  # Keep local execution snappy but record 10s duration in trace logs
            else:
                time.sleep(10.0)

            err_msg = "ConnectionTimeoutError: Failed to connect to db-primary.gcp.internal:5432 after 10000ms"
            _log_structured(err_msg, "CRITICAL", trace_id, "span-database-333")
            raise HTTPException(status_code=500, detail=err_msg)

        # Success scenario
        if IS_MOCK:
            time.sleep(0.01)
        return {"service": "database", "query": "SELECT * FROM users LIMIT 1", "rows": 1}
