"""Unit tests for GCP Observability Tools (Metrics)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from sre_agent.gcp_tools import analyze_trace_cascade, generate_post_mortem, list_metric_descriptors, query_metrics

from sre_agent import gcp_tools

# Checked-in telemetry fixtures. These are what `app/main.py:_generate_mock_trace`
# and `app/main.py:_log_structured` write into `mock_telemetry_data/` when the
# chaos-monkey app is driven with `trigger_error=True` - identically, bar the random
# trace ID and the wall-clock log timestamps, which are rewritten here to describe the
# ten-second incident the spans encode. `test/test_telemetry_fixtures.py` drives the
# app and diffs the result against these files, so that is a checked claim rather than
# a comment. They are committed on purpose: `mock_telemetry_data/` is gitignored, so a
# test that reads from it only passes on the machine that last ran
# `simulate_incident.py`.
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# The trace ID of the committed incident fixture (gateway -> backend -> database,
# with a 10.2 s database connection timeout as the bottleneck).
FIXTURE_TRACE_ID = "06f96234b89348488f6a2a01b1fc4632"


class TestGcpToolsMetrics(unittest.IsolatedAsyncioTestCase):
    """Unit tests for metrics-reading GCP tools."""

    async def test_query_metrics_mock(self) -> None:
        """Verifies that query_metrics returns filtered mock metrics when in mock mode."""
        # Write the fixture into a throwaway directory rather than the repo's
        # gitignored `mock_telemetry_data/`, so the test never touches the working tree.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mock_dir = tmp.name
        metrics_file = os.path.join(mock_dir, "metrics.json")

        mock_metrics = [
            {
                "metric": {
                    "type": "run.googleapis.com/container/cpu/utilizations",
                    "labels": {"service_name": "sre-chaos-monkey"},
                },
                "points": [{"value": {"double_value": 0.15}}],
            }
        ]

        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(mock_metrics, f)

        # Ensure we are testing mock mode
        with mock.patch("sre_agent.gcp_tools.IS_MOCK", True), mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", mock_dir):
            # Query for CPU utilization of sre-chaos-monkey
            result_str = await query_metrics(
                filter_expression='metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="sre-chaos-monkey"'
            )
            result = json.loads(result_str)

            # Assert we found matching metric
            self.assertIsInstance(result, list)
            self.assertTrue(len(result) > 0)
            self.assertEqual(result[0]["metric"]["type"], "run.googleapis.com/container/cpu/utilizations")
            self.assertEqual(result[0]["metric"]["labels"]["service_name"], "sre-chaos-monkey")

            # Query for non-existent service metric
            result_str_missing = await query_metrics(
                filter_expression='metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="non-existent"'
            )
            result_missing = json.loads(result_str_missing)
            self.assertEqual(len(result_missing), 0)

    async def test_list_metric_descriptors_mock(self) -> None:
        """Verifies list_metric_descriptors mock behavior."""
        with mock.patch("sre_agent.gcp_tools.IS_MOCK", True):
            # Query all
            result_str = await list_metric_descriptors()
            result = json.loads(result_str)
            self.assertIsInstance(result, list)
            self.assertTrue(len(result) >= 3)

            # Query with filter
            result_str_filtered = await list_metric_descriptors(filter_expression="postgresql")
            result_filtered = json.loads(result_str_filtered)
            self.assertEqual(len(result_filtered), 1)
            self.assertIn("postgresql", result_filtered[0]["type"])

    async def test_analyze_trace_cascade_mock(self) -> None:
        """Verifies analyze_trace_cascade correctly parses trace spans and identifies the bottleneck in mock mode."""
        with (
            mock.patch("sre_agent.gcp_tools.IS_MOCK", True),
            mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", FIXTURE_DIR),
        ):
            report = await analyze_trace_cascade(FIXTURE_TRACE_ID)
            self.assertIn("Multi-Service Cascade Latency & Bottleneck Analysis", report)
            self.assertIn("Identified Bottleneck", report)
            self.assertIn("/api/database", report)
            # The database span owns 10200 ms of self-time out of a 10270 ms trace -
            # 99.3% - so it must be the reported bottleneck rather than an upstream tier,
            # each of which contributes only its own tens of milliseconds.
            self.assertIn("**Bottleneck Span**: `/api/database`", report)
            self.assertIn("**Total Trace Duration**: `10270 ms`", report)
            self.assertIn("**Self-Execution Time**: `10200 ms` (99.3% of total trace)", report)

    async def test_generate_post_mortem_mock(self) -> None:
        """Verifies generate_post_mortem generates a structured markdown post-mortem report in mock mode."""
        with (
            mock.patch("sre_agent.gcp_tools.IS_MOCK", True),
            mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", FIXTURE_DIR),
        ):
            report = await generate_post_mortem(FIXTURE_TRACE_ID)
            self.assertIn("Incident Post-Mortem", report)
            self.assertIn("Incident Timeline", report)
            self.assertIn("Root Cause Analysis (RCA)", report)
            self.assertIn("ConnectionTimeoutError", report)
            # The timeline quotes the gateway's error log verbatim. `app/main.py` logs
            # the *incoming* exception's detail there - the database's own message, not
            # the `Internal Server Error` it re-raises with. Pin the rendered line so
            # the fixture cannot drift back and take the post-mortem with it.
            self.assertIn(
                "Gateway received error from backend: ConnectionTimeoutError: "
                "Failed to connect to db-primary.gcp.internal:5432 after 10000ms",
                report,
            )
            # Assert the report is actually derived from the fixture telemetry and
            # not just the tool's static RCA boilerplate.
            self.assertIn(f"**Trace ID**: `{FIXTURE_TRACE_ID}`", report)
            self.assertIn("**Impact Duration**: `10270 ms`", report)
            self.assertIn("**Root Service**: `gateway`", report)

    async def test_generate_post_mortem_tolerates_payloadless_error_log(self) -> None:
        """Verifies an ERROR entry with neither payload does not crash the post-mortem.

        Cloud Run's own request log for a 500 response carries the trace ID and ERROR
        severity but no text or JSON payload, so real Cloud Logging returns it with
        both payload fields set to None.
        """
        real_query_logs = gcp_tools.query_logs_by_trace

        async def query_logs_with_payloadless_entry(trace_id: str, project_id: str | None = None) -> str:
            logs = json.loads(await real_query_logs(trace_id, project_id))
            logs.append({"severity": "ERROR", "text_payload": None, "json_payload": None})
            return json.dumps(logs)

        with (
            mock.patch("sre_agent.gcp_tools.IS_MOCK", True),
            mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", FIXTURE_DIR),
            mock.patch("sre_agent.gcp_tools.query_logs_by_trace", query_logs_with_payloadless_entry),
        ):
            report = await generate_post_mortem(FIXTURE_TRACE_ID)
            self.assertIn("Incident Post-Mortem", report)
            # The payload-less entry must not overwrite the error message found before it.
            self.assertIn("Gateway received error from backend: ConnectionTimeoutError", report)

    async def test_analyze_trace_cascade_unknown_trace(self) -> None:
        """Verifies analyze_trace_cascade reports a clean error when the trace is absent."""
        with (
            mock.patch("sre_agent.gcp_tools.IS_MOCK", True),
            mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", FIXTURE_DIR),
        ):
            report = await analyze_trace_cascade("0" * 32)
            self.assertIn("Error retrieving trace cascade", report)

    async def test_analyze_trace_cascade_overlapping_children_and_duplicate_span(self) -> None:
        """Verifies self time counts overlapping child time once and duplicate spans once.

        Real Cloud Trace data has both: concurrent child spans, and the Trace API returning
        Cloud Run's root span twice. Summing child durations understated the parent's self
        time (2000 ms instead of 4000 ms here), and the duplicate rendered the tree twice.
        """

        def span(name: str, span_id: str, parent: str | None, start_ms: int, end_ms: int) -> dict[str, object]:
            return {
                "name": name,
                "spanId": span_id,
                "parentSpanId": parent,
                "startTime": f"2026-10-03T12:00:{start_ms // 1000:02d}.{start_ms % 1000:03d}Z",
                "endTime": f"2026-10-03T12:00:{end_ms // 1000:02d}.{end_ms % 1000:03d}Z",
                "status": "OK",
                "error_message": None,
            }

        root = span("/api/gateway", "1", None, 0, 10000)
        trace_details = {
            "traceId": "a" * 32,
            "root_span": "/api/gateway",
            "durationMs": 10000,
            "error": False,
            "spans": [
                root,
                dict(root),
                span("/api/backend", "2", "1", 1000, 6000),
                span("/api/cache", "3", "1", 4000, 7000),
            ],
        }
        with mock.patch(
            "sre_agent.gcp_tools.get_trace_details", mock.AsyncMock(return_value=json.dumps(trace_details))
        ):
            report = await analyze_trace_cascade("a" * 32)

        # The children cover 1000-7000 ms: 6000 ms, not 5000 + 3000 = 8000 ms.
        self.assertIn("| `/api/gateway` | `1` | `None` | OK | 10000 ms | 4000 ms | 40.0% |", report)
        self.assertEqual(1, report.count("| `/api/gateway` |"))
        self.assertIn("**Bottleneck Span**: `/api/backend` (`2`)", report)
