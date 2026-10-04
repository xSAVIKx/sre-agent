"""Tests for the metrics the target app reports in mock mode (workshop step 2)."""

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
for path in (REPO_ROOT, REPO_ROOT / "sre_agent" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import main as chaos_monkey  # noqa: E402
from sre_agent import gcp_tools  # noqa: E402

# The exact filters the SRE workflow's deterministic tier queries.
CPU_FILTER = (
    'metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="sre-chaos-monkey"'
)
DB_FILTER = (
    'metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" '
    'AND resource.labels.database_id="db-primary"'
)


class TestMockMetrics(unittest.TestCase):
    def test_incident_saturates_the_connection_pool(self) -> None:
        """During the incident the latest DB connection reading is at the pool limit."""
        series = chaos_monkey._mock_metric_series(trigger_error=True)
        db = [s for s in series if s["metric"]["labels"].get("database_id") == "db-primary"]
        self.assertEqual(len(db), 1, "expected one db-primary connection-count series")
        self.assertEqual(db[0]["points"][-1]["value"], chaos_monkey.DB_MAX_CONNECTIONS)

    def test_cpu_is_a_fraction(self) -> None:
        """CPU utilization is reported as a 0-1 fraction, like Cloud Monitoring does."""
        series = chaos_monkey._mock_metric_series(trigger_error=True)
        cpu = [s for s in series if s["metric"]["labels"].get("service_name") == "sre-chaos-monkey"]
        self.assertEqual(len(cpu), 1, "expected one sre-chaos-monkey CPU series")
        self.assertTrue(all(0.0 <= p["value"] <= 1.0 for p in cpu[0]["points"]))

    def test_healthy_request_keeps_incident_metrics(self) -> None:
        """A healthy call after an incident must not reset the saturated readings."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(chaos_monkey, "MOCK_DATA_DIR", tmp):
            chaos_monkey._write_mock_metrics(trigger_error=True)
            chaos_monkey._write_mock_metrics(trigger_error=False)
            with open(f"{tmp}/metrics.json", encoding="utf-8") as f:
                series = json.load(f)
        db = [s for s in series if s["metric"]["labels"].get("database_id") == "db-primary"]
        self.assertEqual(db[0]["points"][-1]["value"], chaos_monkey.DB_MAX_CONNECTIONS)

    def test_query_metrics_finds_what_the_app_wrote(self) -> None:
        """The SRE agent's query_metrics tool must match both series by its filters."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chaos_monkey, "MOCK_DATA_DIR", tmp):
                chaos_monkey._write_mock_metrics(trigger_error=True)
            with mock.patch.object(gcp_tools, "IS_MOCK", True), mock.patch.object(gcp_tools, "MOCK_DATA_DIR", tmp):
                cpu = json.loads(asyncio.run(gcp_tools.query_metrics(CPU_FILTER)))
                db = json.loads(asyncio.run(gcp_tools.query_metrics(DB_FILTER)))
        self.assertEqual(len(cpu), 1)
        self.assertEqual(len(db), 1)


if __name__ == "__main__":
    unittest.main()
