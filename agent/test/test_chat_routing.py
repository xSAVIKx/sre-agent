"""Tests for policy-gated chat routing and the Orchestrator's SRE skill tools over A2A."""

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

    def test_orchestrator_policy_allows_only_the_sre_skill_tools(self) -> None:
        if config.HAS_ANTIGRAVITY:
            self.skipTest("Real SDK policies; covered by test_sdk_contract.")
        policies = config.build_safety_policies()
        for tool in ("list_incidents", "diagnose_sre", "write_post_mortem"):
            self.assertEqual(config.evaluate_mock_policy(policies, tool), "allow")
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

        async def fake_run_diagnosis(
            prompt, project_id=None, refresh=False, conversation_id=None, trace_id=None, ui=False
        ):
            self.calls.append({"project_id": project_id, "refresh": refresh, "conversation_id": conversation_id})
            yield Progress("Fetching traces")
            if self.fail:
                raise RuntimeError("Trace API query failed: 503")
            yield Report(POST_MORTEM)

        async def fake_list_incidents(project_id=None, ui=False):
            self.calls.append({"skill": "list_incidents", "project_id": project_id})
            yield Report("| incident table |", {"kind": "incident_list", "incidents": []})

        async def fake_post_mortem(prompt="", project_id=None, trace_id=None, ui=False):
            self.calls.append({"skill": "write_post_mortem", "project_id": project_id, "trace_id": trace_id})
            yield Report(POST_MORTEM)

        for name, fake in (
            ("run_diagnosis", fake_run_diagnosis),
            ("run_list_incidents", fake_list_incidents),
            ("run_post_mortem", fake_post_mortem),
        ):
            patcher = mock.patch.object(a2a_agent, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = a2a_agent.build_a2a_app(self.BASE_URL)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        self.addAsyncCleanup(lifespan.__aexit__, None, None, None)
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=self.BASE_URL, timeout=30)
        self.addAsyncCleanup(http.aclose)

        from sre_common.a2a_client import clear_card_cache

        clear_card_cache()
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

    async def test_each_tool_calls_its_own_skill(self) -> None:
        sink = config.DiagnosisSink()
        token = config.diagnosis_sink.set(sink)
        try:
            self.assertEqual(await config.list_incidents(project_id="demo"), "| incident table |")
            self.assertEqual(sink.skill, "list_incidents")
            self.assertEqual(await config.write_post_mortem("write it up", trace_id="t" * 32), POST_MORTEM)
            self.assertEqual(sink.skill, "write_post_mortem")
        finally:
            config.diagnosis_sink.reset(token)
        self.assertEqual(
            self.calls,
            [
                {"skill": "list_incidents", "project_id": "demo"},
                {"skill": "write_post_mortem", "project_id": os.environ.get("GCP_PROJECT"), "trace_id": "t" * 32},
            ],
        )

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
        self.assertEqual(events[-1]["rendered"], POST_MORTEM, "without a surface, the report's Markdown")

    def test_blocked_policy_never_reaches_the_sub_agent(self) -> None:
        called = mock.AsyncMock(return_value=POST_MORTEM)
        with (
            mock.patch.object(config, "diagnose_sre", called),
            mock.patch.object(config, "build_safety_policies", lambda: [config.deny("*")]),
        ):
            events = self._chat("Diagnose the latency spike")

        called.assert_not_awaited()
        self.assertIn("blocked by the safety policy", events[-1]["response"])

    def test_failure_question_lists_incidents_and_replies_with_summary_and_card(self) -> None:
        table = "## 📋 Recent incidents\n\n1 failing request.\n\n| # | Trace ID |\n|---|---|\n| 1 | `abc` |"

        surface = [{"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": config.SRE_CATALOG_ID}}]

        async def fake_list(project_id: str | None = None) -> str:
            sink = config.diagnosis_sink.get()
            sink.report, sink.a2ui = table, surface
            return table

        with mock.patch.object(config, "list_incidents", fake_list):
            events = self._chat("What are the latest failures?")

        thoughts = [e["text"] for e in events if e["type"] == "thought"]
        self.assertIn("🔧 Calling tool `list_incidents`...", thoughts)
        done = events[-1]
        self.assertEqual(done["response"], "1 failing request. The full result is below.")
        self.assertEqual(done["a2ui"], surface)
        self.assertEqual(done["rendered"], "")

    def test_surface_actions_become_chat_turns(self) -> None:
        called = mock.AsyncMock(return_value=POST_MORTEM)
        trace = "1c65bf87e4be434ea6d6d7edc1ef8c97"
        action = {"name": "write_post_mortem", "context": {"traceId": trace}, "surfaceId": "sre-1"}
        with mock.patch.object(config, "write_post_mortem", called):
            resp = self.client.post("/chat", json={"action": action})
        events = _sse_events(resp.text)
        self.assertEqual(events[0]["prompt"], f"Write the post-mortem for trace {trace}.")
        called.assert_awaited_once()
        self.assertEqual(called.await_args.kwargs.get("trace_id"), trace)

    def test_unknown_or_malformed_actions_are_rejected(self) -> None:
        for action in (
            {"name": "restart_service", "context": {"traceId": "a" * 32}},
            {"name": "diagnose_incident", "context": {"traceId": "x; rm -rf /"}},
        ):
            with self.subTest(action=action["name"]):
                self.assertEqual(self.client.post("/chat", json={"action": action}).status_code, 422)
        self.assertEqual(self.client.post("/chat", json={"prompt": " "}).status_code, 422)

    def test_the_renderer_bundle_is_served(self) -> None:
        resp = self.client.get("/static/sre-a2ui.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/javascript", resp.headers["content-type"])
        self.assertEqual(resp.headers["cache-control"], "no-cache", "a deploy must reach browsers at once")
        self.assertIn(config.SRE_CATALOG_ID, resp.text, "the bundle implements the catalog the agent requests")

    def test_general_prompt_does_not_call_diagnose_sre(self) -> None:
        called = mock.AsyncMock(return_value=POST_MORTEM)
        with mock.patch.object(config, "diagnose_sre", called):
            events = self._chat("hello")
        called.assert_not_awaited()
        self.assertEqual(events[-1]["type"], "done")


class TestMockRoute(unittest.TestCase):
    """Simulation mode's stand-in for the model choosing among the SRE skill tools."""

    def test_routes(self) -> None:
        trace = "1c65bf87e4be434ea6d6d7edc1ef8c97"
        cases = {
            "What are the latest failures?": ("list_incidents", {}),
            "is anything broken?": ("list_incidents", {}),
            f"Write the post-mortem for {trace}": (
                "write_post_mortem",
                {"prompt": f"Write the post-mortem for {trace}", "trace_id": trace},
            ),
            f"why did {trace} fail?": ("diagnose_sre", {"prompt": f"why did {trace} fail?", "trace_id": trace}),
            "Diagnose the latest failure": ("diagnose_sre", {"prompt": "Diagnose the latest failure"}),
            "latency is spiking": ("diagnose_sre", {"prompt": "latency is spiking"}),
            "hello": None,
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(config.mock_route(prompt), expected)


class TestDiagnoseSreModeSelection(unittest.IsolatedAsyncioTestCase):
    """Every SRE call goes over A2A: to SRE_AGENT_URL, or on a laptop to a locally started SRE agent."""

    async def test_sre_agent_url_forces_a2a_even_in_mock_mode(self) -> None:
        call = mock.AsyncMock(return_value=mock.Mock(text="report"))
        env = {"MOCK_GCP": "true", "SRE_AGENT_URL": "http://sre-agent:8080"}
        with mock.patch.dict(os.environ, env), mock.patch.object(config, "call_agent", call):
            self.assertEqual(await config.diagnose_sre("diagnose"), "report")
        call.assert_awaited_once()
        self.assertEqual(call.await_args.args[0], "http://sre-agent:8080")
        self.assertEqual(call.await_args.args[2]["skill"], "diagnose_incident")

    async def test_laptop_runs_start_the_sre_agent_locally(self) -> None:
        call = mock.AsyncMock(return_value=mock.Mock(text="report"))
        local = mock.Mock(return_value="http://127.0.0.1:9999")
        with (
            mock.patch.dict(os.environ, {"MOCK_GCP": "true", "SRE_AGENT_URL": ""}),
            mock.patch.object(config, "call_agent", call),
            mock.patch.object(config, "local_sre_agent_url", local),
        ):
            await config.diagnose_sre("diagnose")
        self.assertEqual(call.await_args.args[0], "http://127.0.0.1:9999")


if __name__ == "__main__":
    unittest.main()


class TestA2uiClient(unittest.IsolatedAsyncioTestCase):
    """The Orchestrator asks for (and keeps) the A2UI surfaces its chat UI renders."""

    def test_capabilities_name_the_catalog_the_sre_agent_builds(self) -> None:
        from sre_agent import a2ui_surfaces

        self.assertEqual(config.A2UI_CLIENT_CAPABILITIES, a2ui_surfaces.client_capabilities())
        self.assertEqual(config.A2UI_EXTENSION_URI, a2ui_surfaces.A2UI_EXTENSION_URI)

    async def test_chat_calls_request_surfaces_and_keep_them(self) -> None:
        surface = [{"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": config.SRE_CATALOG_ID}}]
        call = mock.AsyncMock(return_value=mock.Mock(text="| table |", a2ui=surface))
        sink = config.DiagnosisSink()
        token = config.diagnosis_sink.set(sink)
        try:
            with (
                mock.patch.dict(os.environ, {"SRE_AGENT_URL": "http://sre-agent:8080"}),
                mock.patch.object(config, "call_agent", call),
            ):
                await config.list_incidents()
        finally:
            config.diagnosis_sink.reset(token)
        metadata = call.await_args.args[2]
        self.assertEqual(metadata["a2uiClientCapabilities"], config.A2UI_CLIENT_CAPABILITIES)
        self.assertEqual(call.await_args.kwargs["extensions"], [config.A2UI_EXTENSION_URI])
        self.assertEqual(sink.a2ui, surface)

    async def test_non_chat_calls_do_not_ask_for_ui(self) -> None:
        call = mock.AsyncMock(return_value=mock.Mock(text="report", a2ui=[]))
        with (
            mock.patch.dict(os.environ, {"SRE_AGENT_URL": "http://sre-agent:8080"}),
            mock.patch.object(config, "call_agent", call),
        ):
            await config.diagnose_sre("diagnose")
        self.assertNotIn("a2uiClientCapabilities", call.await_args.args[2])

    async def test_laptop_runs_get_the_same_surfaces_over_a2a(self) -> None:
        import json

        from sre_agent import diagnosis

        traces = [{"traceId": "a" * 32, "service": "app", "name": "/api", "error": True, "startTime": "t"}]
        sink = config.DiagnosisSink()
        token = config.diagnosis_sink.set(sink)
        try:
            with (
                mock.patch.dict(os.environ, {"MOCK_GCP": "true", "SRE_AGENT_URL": ""}),
                mock.patch.object(diagnosis, "query_traces", mock.AsyncMock(return_value=json.dumps(traces))),
            ):
                await config.list_incidents()
        finally:
            config.diagnosis_sink.reset(token)
        self.assertEqual(sink.a2ui[0]["createSurface"]["catalogId"], config.SRE_CATALOG_ID)
