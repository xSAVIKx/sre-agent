"""OpenTelemetry tracing for the agent services, exported to Cloud Trace.

`setup_tracing` gives a service:

* a tracer provider that exports to Cloud Trace, so the `@otel_trace` spans and
  ADK's built-in agent / LLM / tool spans are recorded instead of dropped;
* a server span per incoming request, parented on the caller's ``traceparent``
  (Cloud Run forwards it), so a request joins the trace that called it;
* a client span per outgoing ``httpx`` request, which also *sends* ``traceparent``
  - A2A calls to the next agent and Gemini calls show up in the same trace.

Together, one chat turn becomes one trace: Orchestrator -> SRE engine ->
Inventory, with the time each hop and each model call took.
"""

import logging
import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

logger = logging.getLogger("sre_common.tracing")

# Health checks would otherwise fill Cloud Trace with one-span traces.
EXCLUDED_URLS = "health,favicon.ico"


def tracing_enabled() -> bool:
    """Export only against a real GCP project: in mock mode there is no Cloud Trace."""
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    return os.getenv("MOCK_GCP", "true").lower() not in ("true", "1", "yes")


def setup_tracing(app: FastAPI | None, service_name: str, exporter: SpanExporter | None = None) -> bool:
    """Configures tracing for one service. Safe to call when tracing is disabled.

    Args:
        app: The service's FastAPI app; its requests get server spans.
        service_name: Reported as the ``service.name`` resource attribute.
        exporter: Where spans go. Defaults to Cloud Trace; tests pass an in-memory one.

    Returns:
        True if tracing was enabled.
    """
    if exporter is None and not tracing_enabled():
        return False

    if exporter is None:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter = CloudTraceSpanExporter()
        except Exception as e:  # e.g. no credentials: run without tracing, not without the service
            logger.warning(f"Cloud Trace exporter unavailable, tracing disabled: {e}")
            return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS)
    logger.info(f"Tracing enabled for {service_name}: exporting spans to Cloud Trace")
    return True
