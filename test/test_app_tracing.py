"""Tests for how `app/main.py` traces requests and reports errors.

FastAPI 0.142+ creates its own request spans. When the app also parented its manual spans
on the caller's `traceparent` header, each request produced two sibling span hierarchies,
and the SRE agent's cascade analysis named a framework span as the bottleneck.
"""

import asyncio
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import httpx
from fastapi import HTTPException
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


class TestAppErrorReporting(unittest.TestCase):
    """Covers how errors are passed up from the database to the gateway's response."""

    DB_ERROR = "ConnectionTimeoutError: Failed to connect to db-primary.gcp.internal:5432 after 10000ms"

    def test_downstream_error_reads_detail(self) -> None:
        """The caller reports the downstream service's error, whether detail is a string or a dict."""
        request = httpx.Request("GET", "http://localhost:8080/api/database")
        for body in ({"detail": self.DB_ERROR}, {"detail": {"error": self.DB_ERROR, "trace_id": TRACE_ID}}):
            response = httpx.Response(500, json=body, request=request)
            self.assertEqual(self.DB_ERROR, chaos_monkey._downstream_error(response))

    def test_downstream_error_without_json_body(self) -> None:
        """A non-JSON error response still produces a message naming the status and path."""
        request = httpx.Request("GET", "http://localhost:8080/api/database")
        response = httpx.Response(502, text="Bad Gateway", request=request)
        self.assertEqual("HTTP 502 from /api/database", chaos_monkey._downstream_error(response))

    def test_exception_message_never_empty(self) -> None:
        """httpx timeouts have an empty message; the exception type stands in for it."""
        self.assertEqual("ReadTimeout", chaos_monkey._exception_message(httpx.ReadTimeout("")))
        self.assertEqual("boom", chaos_monkey._exception_message(ValueError("boom")))

    def test_gateway_response_carries_database_error(self) -> None:
        """In mock mode, the gateway's 500 response names the database error, not a generic one."""
        with (
            tempfile.TemporaryDirectory() as mock_dir,
            mock.patch.object(chaos_monkey, "IS_MOCK", True),
            mock.patch.object(chaos_monkey, "MOCK_DATA_DIR", mock_dir),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(HTTPException) as raised,
        ):
            asyncio.run(chaos_monkey.gateway(mock.Mock(), trigger_error=True))
        self.assertEqual(self.DB_ERROR, raised.exception.detail["error"])


if __name__ == "__main__":
    unittest.main()
