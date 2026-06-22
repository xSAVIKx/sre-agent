"""Unit tests for GCP Observability Tools (Metrics)."""

import os
import json
import unittest
from unittest import mock
from sre_agent.gcp_tools import query_metrics, list_metric_descriptors


class TestGcpToolsMetrics(unittest.IsolatedAsyncioTestCase):
    """Unit tests for metrics-reading GCP tools."""

    async def test_query_metrics_mock(self) -> None:
        """Verifies that query_metrics returns filtered mock metrics when in mock mode."""
        # Ensure we are testing mock mode
        with mock.patch("sre_agent.gcp_tools.IS_MOCK", True), \
             mock.patch("sre_agent.gcp_tools.MOCK_DATA_DIR", "mock_telemetry_data"):
            
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
