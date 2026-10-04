"""Tests for policy-gated chat routing and the diagnose_sre A2A stream."""

import functools
import json
import os
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import config, routes

POST_MORTEM = "# 🚨 Incident Post-Mortem\n\nRoot cause: database connection timeout."


def _sse_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class TestMockPolicyEvaluation(unittest.TestCase):
    """The simulation-mode policy check must match the SDK's precedence rules."""

    def test_orchestrator_policy_allows_only_diagnose_sre(self) -> None:
        if config.HAS_ANTIGRAVITY:
            self.skipTest("Real SDK policies; covered by test_sdk_contract.")
        policies = config.build_safety_policies()
        self.assertEqual(config.evaluate_mock_policy(policies, "diagnose_sre"), "allow")
        self.assertEqual(config.evaluate_mock_policy(policies, "run_command"), "deny")

    def test_specific_deny_beats_specific_allow(self) -> None:
        policies = [config.MockPolicy("t", "allow"), config.MockPolicy("t", "deny")]
        self.assertEqual(config.evaluate_mock_policy(policies, "t"), "deny")

    def test_specific_ask_beats_wildcard_deny(self) -> None:
        policies = [config.MockPolicy("*", "deny"), config.MockPolicy("t", "ask_user")]
        self.assertEqual(config.evaluate_mock_policy(policies, "t"), "ask_user")

    def test_no_rules_fails_closed(self) -> None:
        self.assertEqual(config.evaluate_mock_policy([], "diagnose_sre"), "deny")


class TestDiagnoseSreOverA2A(unittest.IsolatedAsyncioTestCase):
    """`diagnose_sre` talks to the SRE engine's real A2A server (in-process, fake pipeline)."""

    BASE_URL = "http://sre-agent.test"

    async def asyncSetUp(self) -> None:
        from sre_agent.diagnosis import Progress, Report

        from sre_agent import a2a_agent

        self.calls: list[dict] = []
        self.fail = False

        async def fake_run_diagnosis(prompt, project_id=None, refresh=False, conversation_id=None):
            self.calls.append({"project_id": project_id, "refresh": refresh, "conversation_id": conversation_id})
            yield Progress("Fetching traces")
            if self.fail:
                raise RuntimeError("Trace API query failed: 503")
            yield Report(POST_MORTEM)

        patcher = mock.patch.object(a2a_agent, "run_diagnosis", fake_run_diagnosis)
        patcher.start()
        self.addCleanup(patcher.stop)

        app = a2a_agent.build_a2a_app(self.BASE_URL)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        self.addAsyncCleanup(lifespan.__aexit__, None, None, None)
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=self.BASE_URL, timeout=30)
        self.addAsyncCleanup(http.aclose)

        # Route the Orchestrator's A2A client to the in-process server.
        call_agent = functools.partial(config.call_agent, http=http)
        for patch in (
            mock.patch.object(config, "call_agent", call_agent),
            mock.patch.dict(os.environ, {"SRE_AGENT_URL": self.BASE_URL, "MOCK_GCP": "false"}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    async def _diagnose(self, sink: config.DiagnosisSink) -> str:
        token = config.diagnosis_sink.set(sink)
        try:
            return await config.diagnose_sre("Diagnose it", project_id="demo", refresh=True)
        finally:
            config.diagnosis_sink.reset(token)

    async def test_progress_streams_and_report_returns(self) -> None:
        sink = config.DiagnosisSink(context_id="conversation-7")
        report = await self._diagnose(sink)

        self.assertEqual(report, POST_MORTEM)
        self.assertEqual(sink.report, POST_MORTEM)
        self.assertIn("Fetching traces", sink.progress)
        self.assertNotIn(POST_MORTEM, sink.progress, "the report is the artifact, not a progress line")
        self.assertEqual(self.calls, [{"project_id": "demo", "refresh": True, "conversation_id": "conversation-7"}])

    async def test_failed_task_becomes_an_error_report(self) -> None:
        self.fail = True
        report = await self._diagnose(config.DiagnosisSink())
        self.assertTrue(report.startswith("Error:"), report)
        self.assertEqual(len(self.calls), 1, "a failed task is not retried")


class TestChatRouting(unittest.TestCase):
    """Every /chat prompt goes through the Orchestrator agent and its policy."""

    def setUp(self) -> None:
        if config.HAS_ANTIGRAVITY:
            self.skipTest("Exercises the simulation-mode agent; unset GEMINI_API_KEY to run.")
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def _chat(self, prompt: str) -> list[dict]:
        resp = self.client.post("/chat", json={"prompt": prompt})
        self.assertEqual(resp.status_code, 200)
        return _sse_events(resp.text)

    def test_diagnosis_prompt_calls_diagnose_sre_through_the_agent(self) -> None:
        async def fake_diagnose(prompt: str, project_id: str | None = None, refresh: bool = False) -> str:
            config._emit_progress("sub-agent progress")
            sink = config.diagnosis_sink.get()
            sink.report = POST_MORTEM
            return POST_MORTEM

        with mock.patch.object(config, "diagnose_sre", fake_diagnose):
            events = self._chat("Diagnose the latency spike")

        types = [e["type"] for e in events]
        self.assertEqual(types[0], "start")
        self.assertEqual(types[-1], "done")
        thoughts = [e["text"] for e in events if e["type"] == "thought"]
        self.assertIn("🔧 Calling tool `diagnose_sre`...", thoughts)
        self.assertIn("sub-agent progress", thoughts)
        components = [c["type"] for c in events[-1]["response_a2ui"]["components"]]
        self.assertIn("download_button", components)

    def test_blocked_policy_never_reaches_the_sub_agent(self) -> None:
        called = mock.AsyncMock(return_value=POST_MORTEM)
        with (
            mock.patch.object(config, "diagnose_sre", called),
            mock.patch.object(config, "build_safety_policies", lambda: [config.deny("*")]),
        ):
            events = self._chat("Diagnose the latency spike")

        called.assert_not_awaited()
        self.assertIn("blocked by the safety policy", events[-1]["response"])

    def test_general_prompt_does_not_call_diagnose_sre(self) -> None:
        called = mock.AsyncMock(return_value=POST_MORTEM)
        with mock.patch.object(config, "diagnose_sre", called):
            events = self._chat("hello")
        called.assert_not_awaited()
        self.assertEqual(events[-1]["type"], "done")


class TestDiagnoseSreModeSelection(unittest.IsolatedAsyncioTestCase):
    """In mock mode diagnose_sre only runs in-process when no sub-agent URL is set."""

    async def test_sre_agent_url_forces_a2a_even_in_mock_mode(self) -> None:
        call = mock.AsyncMock(return_value=mock.Mock(text="report"))
        env = {"MOCK_GCP": "true", "SRE_AGENT_URL": "http://sre-agent:8080"}
        with mock.patch.dict(os.environ, env), mock.patch.object(config, "call_agent", call):
            self.assertEqual(await config.diagnose_sre("diagnose"), "report")
        call.assert_awaited_once()
        self.assertEqual(call.await_args.args[0], "http://sre-agent:8080")


if __name__ == "__main__":
    unittest.main()
