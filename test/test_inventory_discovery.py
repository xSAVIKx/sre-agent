"""Tests for real (non-mock) infrastructure discovery in the Inventory Agent.

In production the agent never discovered anything real: `google-cloud-run` and
`google-cloud-asset` were not dependencies, `run_job` was called with an
`overrides=` keyword it does not accept, and every failure fell back to caching
the mock topology for the real project.
"""

import asyncio
import datetime
import pathlib
import sys
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
for src in ("inventory_agent/src", "sre_common/src"):
    path = str(REPO_ROOT / src)
    if path not in sys.path:
        sys.path.insert(0, path)

from google.cloud import asset_v1, run_v2  # noqa: E402

from inventory_agent import discovery, routes  # noqa: E402


def _service(name: str, uri: str, connector: str = "") -> run_v2.Service:
    return run_v2.Service(
        name=f"projects/p/locations/us-central1/services/{name}",
        uri=uri,
        template=run_v2.RevisionTemplate(vpc_access=run_v2.VpcAccess(connector=connector)),
    )


class TestRunGcpDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.services = mock.patch.object(run_v2, "ServicesClient").start()
        self.assets = mock.patch.object(asset_v1, "AssetServiceClient").start()
        self.addCleanup(mock.patch.stopall)
        self.services.return_value.list_services.return_value = [
            _service("sre-chaos-monkey", "https://sre-chaos-monkey-1.us-central1.run.app", "sre-vpc"),
            _service("sre-agent", "https://sre-agent-1.us-central1.run.app"),
        ]
        self.assets.return_value.search_all_resources.return_value = [
            asset_v1.ResourceSearchResult(
                name="//firestore.googleapis.com/projects/p/databases/(default)",
                asset_type="firestore.googleapis.com/Database",
                display_name="(default)",
            )
        ]

    def test_maps_services_and_databases(self) -> None:
        resources, metadata = discovery.run_gcp_discovery("p")

        self.services.return_value.list_services.assert_called_once_with(parent="projects/p/locations/-")
        self.assertEqual(
            resources["services"][0],
            {
                "name": "sre-chaos-monkey",
                "url": "https://sre-chaos-monkey-1.us-central1.run.app",
                "region": "us-central1",
                "vpc_connector": "sre-vpc",
            },
        )
        self.assertEqual(resources["databases"], [{"name": "(default)", "type": "FIRESTORE"}])
        self.assertEqual(metadata["resource_count"], 3)

    def test_database_search_is_best_effort(self) -> None:
        self.assets.return_value.search_all_resources.side_effect = RuntimeError("Cloud Asset API disabled")
        resources, _ = discovery.run_gcp_discovery("p")
        self.assertEqual(len(resources["services"]), 2)
        self.assertEqual(resources["databases"], [])

    def test_unlistable_services_raise_instead_of_inventing_topology(self) -> None:
        self.services.return_value.list_services.side_effect = RuntimeError("403 permission denied")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.run_gcp_discovery("p")


class TestScannerJobMain(unittest.TestCase):
    def test_failed_discovery_is_reported_and_fails_the_task(self) -> None:
        env = {"TARGET_PROJECT_ID": "p", "MOCK_GCP": "false", "CALLBACK_URL": "https://inv/callback"}
        with (
            mock.patch.dict("os.environ", env),
            mock.patch.object(sys, "argv", ["discovery"]),
            mock.patch.object(discovery, "run_gcp_discovery", side_effect=discovery.DiscoveryError("boom")),
            mock.patch.object(discovery.httpx, "post", return_value=mock.Mock(status_code=200)) as post,
            self.assertRaises(SystemExit) as exit_,
        ):
            discovery.main()

        self.assertEqual(exit_.exception.code, 1)
        self.assertEqual(post.call_args.kwargs["json"]["status"], "FAILED")


class TestTriggerScannerJob(unittest.TestCase):
    def setUp(self) -> None:
        mock.patch.object(routes, "IS_MOCK", False).start()
        self.jobs = mock.patch.object(run_v2, "JobsClient").start()
        self.jobs.return_value.job_path.return_value = "projects/p/locations/us-central1/jobs/inventory-scanner-job"
        self.set_status = mock.patch.object(routes, "set_project_status", mock.AsyncMock()).start()
        self.mock_scan = mock.patch.object(routes, "run_discovery_mock", mock.AsyncMock()).start()
        mock.patch("sre_common.retry.asyncio.sleep", mock.AsyncMock()).start()
        self.addCleanup(mock.patch.stopall)

    def test_overrides_travel_in_the_request(self) -> None:
        asyncio.run(routes.trigger_scanner_job("p"))

        request = self.jobs.return_value.run_job.call_args.kwargs["request"]
        self.assertEqual(request["name"], "projects/p/locations/us-central1/jobs/inventory-scanner-job")
        env = {e["name"]: e["value"] for e in request["overrides"]["container_overrides"][0]["env"]}
        self.assertEqual(env["TARGET_PROJECT_ID"], "p")
        self.set_status.assert_not_awaited()

    def test_trigger_failure_marks_failed_and_never_caches_mock_topology(self) -> None:
        self.jobs.return_value.run_job.side_effect = RuntimeError("503 unavailable")
        asyncio.run(routes.trigger_scanner_job("p"))

        self.set_status.assert_awaited_once_with("p", "FAILED")
        self.mock_scan.assert_not_called()


class TestNeedsRescan(unittest.TestCase):
    def test_rescan_rules(self) -> None:
        now = datetime.datetime.now(datetime.UTC)
        self.assertTrue(routes._needs_rescan({"status": "FAILED"}))
        self.assertTrue(
            routes._needs_rescan({"status": "DISCOVERING", "last_update_time": now - routes.STALE_DISCOVERY * 2})
        )
        self.assertFalse(routes._needs_rescan({"status": "DISCOVERING", "last_update_time": now}))
        self.assertFalse(
            routes._needs_rescan({"status": "ACTIVE", "last_update_time": now - routes.STALE_DISCOVERY * 2})
        )


if __name__ == "__main__":
    unittest.main()
