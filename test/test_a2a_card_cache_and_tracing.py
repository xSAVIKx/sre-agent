"""Agent-card caching in the shared A2A client, and trace propagation between services."""

import pathlib
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
for src in ("inventory_agent/src", "sre_common/src"):
    path = str(REPO_ROOT / src)
    if path not in sys.path:
        sys.path.insert(0, path)

import httpx  # noqa: E402
from a2a.client.errors import A2AClientError  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from inventory_agent import a2a_server  # noqa: E402
from sre_common import a2a_client  # noqa: E402

BASE_URL = "http://inventory.test"


class TestAgentCardCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        a2a_client.clear_card_cache()
        self.card_fetches = 0

        async def count(request: httpx.Request) -> None:
            if request.url.path.endswith("/agent-card.json"):
                self.card_fetches += 1

        app = Starlette(routes=a2a_server.build_a2a_routes(BASE_URL))
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL, event_hooks={"request": [count]}
        )
        patcher = mock.patch.object(a2a_server, "lookup_inventory", mock.AsyncMock(return_value={"status": "ACTIVE"}))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.http.aclose()

    async def _call(self) -> None:
        await a2a_client.call_agent(BASE_URL, "topology", {"project_id": "p"}, http=self.http)

    async def test_card_is_fetched_once_per_ttl(self) -> None:
        await self._call()
        await self._call()
        self.assertEqual(self.card_fetches, 1)

    async def test_expired_card_is_refetched(self) -> None:
        with mock.patch.object(a2a_client, "CARD_TTL_SECONDS", 0):
            await self._call()
            await self._call()
        self.assertEqual(self.card_fetches, 2)

    async def test_transport_failure_drops_the_cached_card(self) -> None:
        await self._call()
        self.assertIn(BASE_URL, a2a_client._card_cache)

        broken = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("gone"))),
            base_url=BASE_URL,
        )
        with self.assertRaises((A2AClientError, httpx.HTTPError)):
            await a2a_client.call_agent(BASE_URL, "topology", http=broken)
        await broken.aclose()
        self.assertNotIn(BASE_URL, a2a_client._card_cache)


# Runs in a subprocess: the tracer provider and the httpx instrumentation are process-global.
TRACE_PROPAGATION_SCRIPT = textwrap.dedent(
    """
    import threading, time
    import httpx, uvicorn
    from fastapi import FastAPI, Request
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from sre_common.tracing import setup_tracing

    downstream, upstream = FastAPI(), FastAPI()

    @downstream.get("/work")
    async def work(request: Request):
        return {"traceparent": request.headers.get("traceparent")}

    @upstream.get("/chat")
    async def chat():
        async with httpx.AsyncClient() as client:
            return (await client.get("http://127.0.0.1:18902/work")).json()

    exporter = InMemorySpanExporter()
    assert setup_tracing(upstream, "upstream", exporter=exporter)
    FastAPIInstrumentor.instrument_app(downstream, tracer_provider=trace.get_tracer_provider())

    for app, port in ((upstream, 18901), (downstream, 18902)):
        server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1.5)

    seen = httpx.get("http://127.0.0.1:18901/chat").json()["traceparent"]
    trace.get_tracer_provider().force_flush()
    spans = exporter.get_finished_spans()
    trace_ids = {format(s.context.trace_id, "032x") for s in spans}
    kinds = sorted({s.kind.name for s in spans})
    print("TRACEPARENT", seen)
    print("TRACE_IDS", len(trace_ids), sorted(trace_ids)[0])
    print("KINDS", ",".join(kinds))
    """
)


class TestTracePropagation(unittest.TestCase):
    def test_one_request_is_one_trace_across_services(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", TRACE_PROPAGATION_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PYTHONPATH": str(REPO_ROOT / "sre_common" / "src"), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        lines = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)

        count, trace_id = lines["TRACE_IDS"].split()
        self.assertEqual(count, "1", "server, client and downstream spans must share one trace")
        self.assertIn(trace_id, lines["TRACEPARENT"], "the outgoing call carries the trace context")
        self.assertEqual(lines["KINDS"], "CLIENT,INTERNAL,SERVER")


if __name__ == "__main__":
    unittest.main()
