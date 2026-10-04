"""Unified OpenTelemetry Tracing Utilities for SRE agents.

Provides a fail-safe, unified otel_trace decorator and start_span context manager.
"""

import contextlib
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

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
    """Context manager to start an OpenTelemetry span safely.

    Only *starting* the span is fail-safe. Exceptions raised by the wrapped code are
    recorded on the span and re-raised unchanged: the previous version caught them
    in its fallback handler and yielded a second time, which turned every error
    into ``RuntimeError: generator didn't stop after throw()`` and hid it from
    callers such as ``retry_async``.
    """
    if not HAS_OTEL:
        yield None
        return

    try:
        span_cm = trace.get_tracer(tracer_name).start_as_current_span(name)
        span = span_cm.__enter__()
    except Exception as e:
        logger.debug(f"Failed to start OpenTelemetry span '{name}': {e}")
        yield None
        return

    try:
        yield span
    except BaseException as exc:
        # start_as_current_span records the exception and sets the ERROR status.
        if not span_cm.__exit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        span.set_status(StatusCode.OK)
        span_cm.__exit__(None, None, None)


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
