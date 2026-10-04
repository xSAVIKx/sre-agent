"""The Inventory Agent's A2A server and the shared A2A client, exercised in-process.

Both sides use the real a2a-sdk: requests go through `sre_common.a2a_client.call_agent`
into the Starlette routes over an httpx ASGI transport, so no sockets are opened.
"""

import pathlib
import sys
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
for src in ("inventory_agent/src", "sre_agent/src", "sre_common/src"):
    path = str(REPO_ROOT / src)
    if path not in sys.path:
        sys.path.insert(0, path)

import httpx  # noqa: E402
from a2a.helpers import new_task_from_user_message, new_text_message, new_text_part  # noqa: E402
from a2a.server.agent_execution import AgentExecutor  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater  # noqa: E402
from sre_common.a2a_client import A2ATaskError, call_agent, clear_card_cache  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from inventory_agent import a2a_server  # noqa: E402

BASE_URL = "http://agent.test"


def _client_for(routes) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url=BASE_URL)


class TestInventoryA2A(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_card_cache()  # each test serves a different agent at the same URL

    async def test_topology_comes_back_as_a_data_artifact(self) -> None:
        cached = {"project_id": "demo", "status": "ACTIVE", "discovered_resources": {"services": [{"name": "app"}]}}
        lookup = mock.AsyncMock(return_value=cached)
        with mock.patch.object(a2a_server, "lookup_inventory", lookup):
            async with _client_for(a2a_server.build_a2a_routes(BASE_URL)) as http:
                result = await call_agent(BASE_URL, "topology", {"project_id": "demo", "refresh": True}, http=http)

        lookup.assert_awaited_once_with("demo", refresh=True)
        self.assertEqual(result.data, [cached])

    async def test_firestore_datetimes_are_serialized(self) -> None:
        import datetime

        cached = {"status": "ACTIVE", "last_update_time": datetime.datetime(2026, 10, 4, tzinfo=datetime.UTC)}
        with mock.patch.object(a2a_server, "lookup_inventory", mock.AsyncMock(return_value=cached)):
            async with _client_for(a2a_server.build_a2a_routes(BASE_URL)) as http:
                result = await call_agent(BASE_URL, "topology", {"project_id": "demo"}, http=http)
        self.assertEqual(result.data[0]["last_update_time"], "2026-10-04T00:00:00+00:00")

    async def test_lookup_failure_fails_the_task(self) -> None:
        with mock.patch.object(
            a2a_server, "lookup_inventory", mock.AsyncMock(side_effect=RuntimeError("firestore down"))
        ):
            async with _client_for(a2a_server.build_a2a_routes(BASE_URL)) as http:
                with self.assertRaisesRegex(A2ATaskError, "firestore down"):
                    await call_agent(BASE_URL, "topology", {"project_id": "demo"}, http=http)

    def test_card(self) -> None:
        card = a2a_server.build_agent_card("https://inventory-agent-1.us-central1.run.app")
        self.assertEqual(card.supported_interfaces[0].url, "https://inventory-agent-1.us-central1.run.app/")
        self.assertEqual([s.id for s in card.skills], ["get_topology"])


class _ScriptedExecutor(AgentExecutor):
    """An agent that publishes progress, repeats its result as a status message (as
    ADK's executor does) and then emits it as the artifact."""

    def __init__(self, fail: bool = False, artifact: bool = True) -> None:
        self.fail, self.artifact = fail, artifact

    async def execute(self, context, event_queue) -> None:
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        for text in ("step 1", "step 2", "the report"):
            await updater.start_work(new_text_message(text, task.context_id, task.id))
        if self.fail:
            await updater.failed(new_text_message("boom", task.context_id, task.id))
            return
        if self.artifact:
            await updater.add_artifact([new_text_part("the report")])
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:
        raise NotImplementedError


def _scripted_routes(executor: AgentExecutor):
    card = a2a_server.build_agent_card(BASE_URL)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return [*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")]


class TestCallAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_card_cache()

    async def test_progress_excludes_the_repeated_result(self) -> None:
        progress: list[str] = []
        async with _client_for(_scripted_routes(_ScriptedExecutor())) as http:
            result = await call_agent(BASE_URL, "go", on_progress=progress.append, http=http)

        self.assertEqual(progress, ["step 1", "step 2"])
        self.assertEqual(result.text, "the report")
        self.assertTrue(result.task_id)

    async def test_failed_task_raises(self) -> None:
        async with _client_for(_scripted_routes(_ScriptedExecutor(fail=True))) as http:
            with self.assertRaisesRegex(A2ATaskError, "TASK_STATE_FAILED: boom"):
                await call_agent(BASE_URL, "go", http=http)

    async def test_no_artifact_raises(self) -> None:
        async with _client_for(_scripted_routes(_ScriptedExecutor(artifact=False))) as http:
            with self.assertRaisesRegex(A2ATaskError, "without producing an artifact"):
                await call_agent(BASE_URL, "go", http=http)


if __name__ == "__main__":
    unittest.main()
