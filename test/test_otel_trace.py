"""`otel_trace` / `start_span` must never change the exception the wrapped code raises."""

import asyncio
import unittest
from unittest import mock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from sre_common import otel, otel_trace, retry_async, start_span


class TestExceptionsPassThrough(unittest.TestCase):
    def test_sync_function_keeps_its_exception(self) -> None:
        @otel_trace("sync")
        def boom() -> None:
            raise ValueError("original")

        with self.assertRaisesRegex(ValueError, "original"):
            boom()

    def test_async_function_keeps_its_exception(self) -> None:
        @otel_trace("async")
        async def boom() -> None:
            raise KeyError("original")

        with self.assertRaises(KeyError):
            asyncio.run(boom())

    def test_transient_errors_are_still_retried(self) -> None:
        """Masked as RuntimeError, a 503 used to look permanent and was never retried."""
        attempts = []

        @retry_async(max_retries=2, initial_delay=0)
        @otel_trace("flaky")
        async def flaky() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("503 service unavailable")
            return "ok"

        with mock.patch("sre_common.retry.asyncio.sleep", mock.AsyncMock()):
            self.assertEqual(asyncio.run(flaky()), "ok")
        self.assertEqual(len(attempts), 3)


class TestSpanStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        patcher = mock.patch.object(otel.trace, "get_tracer", lambda name: provider.get_tracer(name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_error_is_recorded_on_the_span(self) -> None:
        with self.assertRaises(ValueError), start_span("failing"):
            raise ValueError("original")
        (span,) = self.exporter.get_finished_spans()
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertEqual(span.events[0].name, "exception")

    def test_success_marks_ok(self) -> None:
        with start_span("fine"):
            pass
        (span,) = self.exporter.get_finished_spans()
        self.assertEqual(span.status.status_code, StatusCode.OK)

    def test_span_start_failure_is_fail_safe(self) -> None:
        with (
            mock.patch.object(otel.trace, "get_tracer", side_effect=RuntimeError("no tracer")),
            start_span("x") as span,
        ):
            self.assertIsNone(span)


if __name__ == "__main__":
    unittest.main()
