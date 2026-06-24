"""Unit tests for GCP Observability Tools (Metrics)."""

import os
import json
import unittest
from unittest import mock
from sre_agent.gcp_tools import query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem


class TestGcpToolsMetrics(unittest.IsolatedAsyncioTestCase):
    """Unit tests for metrics-reading GCP tools."""

    async def test_query_metrics_mock(self) -> None:
        """Verifies that query_metrics returns filtered mock metrics when in mock mode."""
        # Create temp mock telemetry directory and file if it doesn't exist
        mock_dir = "mock_telemetry_data"
        os.makedirs(mock_dir, exist_ok=True)
        metrics_file = os.path.join(mock_dir, "metrics.json")
        
        mock_metrics = [
            {
                "metric": {
                    "type": "run.googleapis.com/container/cpu/utilizations",
                    "labels": {
                        "service_name": "sre-chaos-monkey"
                    }
                },
                "points": [
                    {
                        "value": {
                            "double_value": 0.15
                        }
                    }
                ]
            }
        ]
        
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(mock_metrics, f)

        try:
            # Ensure we are testing mock mode
            with mock.patch("sre_agent.gcp_tools.IS_MOCK", True), \
                 mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", mock_dir):
                
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
        finally:
            if os.path.exists(metrics_file):
                os.remove(metrics_file)

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
        trace_id = "2b3ad50bbc544e4a888e7cf886f10b8d"
        with mock.patch("sre_agent.gcp_tools.IS_MOCK", True), \
             mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", "mock_telemetry_data"):
            report = await analyze_trace_cascade(trace_id)
            self.assertIn("Multi-Service Cascade Latency & Bottleneck Analysis", report)
            self.assertIn("Identified Bottleneck", report)
            self.assertIn("/api/database", report)

    async def test_generate_post_mortem_mock(self) -> None:
        """Verifies generate_post_mortem generates a structured markdown post-mortem report in mock mode."""
        trace_id = "2b3ad50bbc544e4a888e7cf886f10b8d"
        with mock.patch("sre_agent.gcp_tools.IS_MOCK", True), \
             mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", "mock_telemetry_data"):
            report = await generate_post_mortem(trace_id)
            self.assertIn("Incident Post-Mortem", report)
            self.assertIn("Incident Timeline", report)
            self.assertIn("Root Cause Analysis (RCA)", report)
            self.assertIn("ConnectionTimeoutError", report)
