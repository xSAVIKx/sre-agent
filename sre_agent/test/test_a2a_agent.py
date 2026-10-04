"""The SRE engine served over A2A, exercised in-process with a real A2A client.

The skill pipelines are replaced by fakes so these tests pin the protocol
mapping, not the diagnosis itself: progress -> WORKING status updates, report ->
task artifact (text + data part), request metadata -> skill and its arguments,
contextId -> session.
"""

import unittest
from unittest import mock

import a2a.types as T
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from google.adk.a2a.converters import request_converter
from sre_agent.diagnosis import Progress, Report
from sre_common.a2a_client import call_agent

from sre_agent import a2a_agent

BASE_URL = "http://sre-agent.test"


class TestAgentCard(unittest.TestCase):
    def test_card_advertises_the_public_url_and_skills(self) -> None:
        card = a2a_agent.build_agent_card("https://sre-sub-agent-1.us-central1.run.app")
        (interface,) = card.supported_interfaces
        self.assertEqual(interface.url, "https://sre-sub-agent-1.us-central1.run.app/")
        self.assertEqual(interface.protocol_binding, "JSONRPC")
        self.assertTrue(card.capabilities.streaming)
        self.assertEqual([s.id for s in card.skills], ["list_incidents", "diagnose_incident", "write_post_mortem"])

    def test_metadata_key_matches_adk(self) -> None:
        """The agent reads request metadata where ADK's request converter files it."""
        self.assertEqual(a2a_agent.A2A_METADATA_KEY, request_converter.A2A_METADATA_KEY)


class TestA2AFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls: list[dict] = []

        async def fake_run_diagnosis(prompt, project_id=None, refresh=False, conversation_id=None, trace_id=None):
            self.calls.append(
                {
                    "skill": "diagnose_incident",
                    "prompt": prompt,
                    "project_id": project_id,
                    "refresh": refresh,
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                }
            )
            yield Progress("step 1")
            yield Progress("step 2")
            yield Report("# 🚨 Incident Post-Mortem\nreport body")

        async def fake_list_incidents(project_id=None):
            self.calls.append({"skill": "list_incidents", "project_id": project_id})
            yield Progress("scanning")
            yield Report("| incidents |", {"kind": "incident_list", "incidents": [{"traceId": "a" * 32}]})

        async def fake_post_mortem(prompt="", project_id=None, trace_id=None):
            self.calls.append({"skill": "write_post_mortem", "project_id": project_id, "trace_id": trace_id})
            yield Report("# 🚨 Incident Post-Mortem", {"kind": "post_mortem", "trace_id": trace_id})

        for name, fake in (
            ("run_diagnosis", fake_run_diagnosis),
            ("run_list_incidents", fake_list_incidents),
            ("run_post_mortem", fake_post_mortem),
        ):
            patcher = mock.patch.object(a2a_agent, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = a2a_agent.build_a2a_app(BASE_URL)
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.addAsyncCleanup(self.lifespan.__aexit__, None, None, None)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, timeout=30)
        self.addAsyncCleanup(self.http.aclose)

    async def _send(self, text: str, metadata: dict, context_id: str = "") -> list[T.StreamResponse]:
        card = await A2ACardResolver(self.http, BASE_URL).get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=self.http, streaming=True)).create(card)
        message = T.Message(role=T.Role.ROLE_USER, parts=[T.Part(text=text)], message_id="m1", context_id=context_id)
        request = T.SendMessageRequest(message=message)
        request.metadata.update(metadata)
        return [event async for event in client.send_message(request)]

    async def test_progress_then_artifact_then_completed(self) -> None:
        events = await self._send("Diagnose it", {"project_id": "demo"})

        working = [
            "".join(p.text for p in e.status_update.status.message.parts)
            for e in events
            if e.HasField("status_update") and e.status_update.status.state == T.TaskState.TASK_STATE_WORKING
        ]
        self.assertIn("step 1", working)
        self.assertIn("step 2", working)

        artifacts = [e.artifact_update.artifact for e in events if e.HasField("artifact_update")]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual("".join(p.text for p in artifacts[0].parts), "# 🚨 Incident Post-Mortem\nreport body")

        self.assertEqual(events[-1].status_update.status.state, T.TaskState.TASK_STATE_COMPLETED)

    async def test_metadata_and_context_reach_the_pipeline(self) -> None:
        await self._send("Diagnose it", {"project_id": "demo", "refresh": True}, context_id="conversation-42")

        (call,) = self.calls
        self.assertEqual(call["prompt"], "Diagnose it")
        self.assertEqual(call["project_id"], "demo")
        self.assertTrue(call["refresh"])
        self.assertEqual(call["conversation_id"], "conversation-42")
        self.assertEqual(call["skill"], "diagnose_incident", "no skill in the metadata means diagnose")

    async def test_skill_metadata_selects_the_pipeline(self) -> None:
        await self._send("which trace?", {"skill": "write_post_mortem", "project_id": "demo", "trace_id": "t1"})
        self.assertEqual(self.calls, [{"skill": "write_post_mortem", "project_id": "demo", "trace_id": "t1"}])

    async def test_structured_result_arrives_as_a_data_part(self) -> None:
        result = await call_agent(BASE_URL, "latest failures?", {"skill": "list_incidents"}, http=self.http)
        self.assertEqual(result.text, "| incidents |")
        self.assertEqual(result.data, [{"kind": "incident_list", "incidents": [{"traceId": "a" * 32}]}])

    async def test_unknown_skill_fails_the_task(self) -> None:
        events = await self._send("hi", {"skill": "restart_everything"})
        self.assertEqual(events[-1].status_update.status.state, T.TaskState.TASK_STATE_FAILED)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
