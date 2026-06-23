"""Unified OpenTelemetry Tracing Utilities for SRE agents.

Provides a fail-safe, unified otel_trace decorator and start_span context manager.
"""

import functools
import inspect
import contextlib
import logging
from typing import Callable, Any

logger = logging.getLogger("sre_common.otel")

# Fail-safe OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


@contextlib.contextmanager
def start_span(name: str, tracer_name: str = "sre_common"):
    """Context manager to start an OpenTelemetry span safely."""
    if HAS_OTEL:
        try:
            tracer = trace.get_tracer(tracer_name)
            with tracer.start_as_current_span(name) as span:
                try:
                    yield span
                    span.set_status(StatusCode.OK)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(StatusCode.ERROR, str(e))
                    raise
        except Exception as e:
            logger.debug(f"Failed to start OpenTelemetry span '{name}': {e}")
            yield None
    else:
        yield None


def otel_trace(span_name: str, tracer_name: str = "sre_common"):
    """Decorator to wrap a function call in a custom OpenTelemetry span.

    Supports both synchronous and asynchronous functions.
    """
    def decorator(func: Callable[..., Any]):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with start_span(span_name, tracer_name):
                    return await func(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with start_span(span_name, tracer_name):
                    return func(*args, **kwargs)
            return sync_wrapper
    return decorator
