"""Tests for how `app/main.py` parents and marks its manual OpenTelemetry spans.

FastAPI 0.142+ creates its own request spans. When the app also parented its manual spans
on the caller's `traceparent` header, each request produced two sibling span hierarchies,
and the SRE agent's cascade analysis named a framework span as the bottleneck.
"""

import pathlib
import sys
import unittest
from unittest import mock

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import main as chaos_monkey  # noqa: E402

TRACE_ID = "4ff14e622f1acfab868fc7649777f7fd"
CALLER_SPAN_ID = "00f067aa0ba902b7"


class TestAppSpanParenting(unittest.TestCase):
    """Covers `_span_context` and `_mark_span_error`."""

    def setUp(self) -> None:
        """Uses a local SDK tracer; nothing is exported."""
        self.tracer = TracerProvider().get_tracer(__name__)
        self.request = mock.Mock(headers={"traceparent": f"00-{TRACE_ID}-{CALLER_SPAN_ID}-01"})

    def test_nests_under_active_request_span(self) -> None:
        """Inside a request span, the manual span starts in the current context."""
        with self.tracer.start_as_current_span("GET /api/database") as request_span:
            context = chaos_monkey._span_context(self.request)
            with self.tracer.start_as_current_span("/api/database", context=context) as manual_span:
                self.assertEqual(request_span.get_span_context().span_id, manual_span.parent.span_id)

    def test_falls_back_to_traceparent_header(self) -> None:
        """Without a request span, the manual span continues the caller's trace."""
        context = chaos_monkey._span_context(self.request)
        with self.tracer.start_as_current_span("/api/database", context=context) as manual_span:
            self.assertEqual(int(TRACE_ID, 16), manual_span.get_span_context().trace_id)
            self.assertEqual(int(CALLER_SPAN_ID, 16), manual_span.parent.span_id)

    def test_mark_span_error_sets_status_and_attributes(self) -> None:
        """Errors are recorded as attributes, which the Cloud Trace v1 API returns as labels."""
        with self.tracer.start_as_current_span("/api/database") as span:
            chaos_monkey._mark_span_error(span, "ConnectionTimeoutError", "timed out")
        self.assertEqual(trace.StatusCode.ERROR, span.status.status_code)
        self.assertEqual("ConnectionTimeoutError", span.attributes["error.type"])
        self.assertEqual("timed out", span.attributes["error.message"])


if __name__ == "__main__":
    unittest.main()
