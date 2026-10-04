"""The caches and the cheaper trace listing that cut a diagnosis by ~15 s on the demo."""

import json
import unittest
from unittest import mock

from sre_agent import gcp_tools, inventory_client


class TestTelemetryCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        gcp_tools._telemetry_cache.clear()
        self.calls = 0

        @gcp_tools._cached_telemetry
        async def lookup(trace_id: str, project_id: str | None = None) -> str:
            self.calls += 1
            return json.dumps({"error": "boom"}) if trace_id == "bad" else json.dumps({"spans": [trace_id]})

        self.lookup = lookup

    async def test_repeated_reads_of_a_trace_hit_the_api_once(self) -> None:
        with mock.patch.object(gcp_tools, "IS_MOCK", False):
            first = await self.lookup("t1", "p")
            second = await self.lookup("t1", "p")
            await self.lookup("t2", "p")
        self.assertEqual(first, second)
        self.assertEqual(self.calls, 2)

    async def test_errors_and_mock_mode_are_not_cached(self) -> None:
        with mock.patch.object(gcp_tools, "IS_MOCK", False):
            await self.lookup("bad", "p")
            await self.lookup("bad", "p")
        with mock.patch.object(gcp_tools, "IS_MOCK", True):
            await self.lookup("t1", "p")
            await self.lookup("t1", "p")
        self.assertEqual(self.calls, 4)

    def test_tool_signatures_survive_the_cache(self) -> None:
        import inspect

        params = list(inspect.signature(gcp_tools.get_trace_details).parameters)
        self.assertEqual(params, ["trace_id", "project_id"])


class TestTopologyCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        inventory_client._topology_cache.clear()

    async def _fetch(self, status: str, **kw) -> mock.AsyncMock:
        call = mock.AsyncMock(return_value=mock.Mock(data=[{"status": status}]))
        with mock.patch.object(inventory_client, "call_agent", call):
            await inventory_client.fetch_topology("p", **kw)
            await inventory_client.fetch_topology("p", **kw)
        return call

    async def test_settled_topology_is_fetched_once(self) -> None:
        self.assertEqual((await self._fetch("ACTIVE")).await_count, 1)

    async def test_discovering_and_refresh_are_not_cached(self) -> None:
        self.assertEqual((await self._fetch("DISCOVERING")).await_count, 2)
        inventory_client._topology_cache.clear()
        self.assertEqual((await self._fetch("ACTIVE", refresh=True)).await_count, 2)


class TestTraceListing(unittest.IsolatedAsyncioTestCase):
    async def test_newest_root_spans_from_one_page(self) -> None:
        trace_v1 = gcp_tools.trace_v1
        root = trace_v1.Trace(
            trace_id="a" * 32,
            spans=[
                trace_v1.TraceSpan(
                    span_id=1,
                    name="/api/gateway",
                    labels={"/http/status_code": "500", "/http/host": "sre-chaos-monkey-1.us-central1.run.app"},
                )
            ],
        )
        client = mock.Mock()
        client.list_traces.return_value.pages = iter([mock.Mock(traces=[root])])

        with (
            mock.patch.object(gcp_tools, "IS_MOCK", False),
            mock.patch.object(gcp_tools.trace_v1, "TraceServiceClient", return_value=client),
        ):
            traces = json.loads(await gcp_tools.query_traces(project_id="p", limit=10))

        request = client.list_traces.call_args.kwargs["request"]
        self.assertEqual(request.view, trace_v1.ListTracesRequest.ViewType.ROOTSPAN)
        self.assertEqual(request.order_by, "start desc")
        self.assertEqual(request.page_size, gcp_tools.TRACE_SCAN_SIZE)
        self.assertEqual(traces[0]["service"], "sre-chaos-monkey")
        self.assertTrue(traces[0]["error"])


if __name__ == "__main__":
    unittest.main()
