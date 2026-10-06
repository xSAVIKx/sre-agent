"""The ADK agents and workflow, run for real with the scripted `SimulatedLlm`.

Workshop step 1 wires the workflow, step 2 gives the LogCorrelator its tools; these
tests check what the agents do, not how the code looks.
"""

import json
import unittest
from unittest import mock

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from sre_agent import simulated_llm, sre_workflow

ERR, SLOW = "a" * 32, "b" * 32
CANDIDATES = [
    {"traceId": ERR, "incident": "error", "service": "gateway"},
    {"traceId": SLOW, "incident": "slow", "service": "gateway"},
]
SPANS = json.dumps({"traceId": ERR, "spans": [{"spanId": "1", "name": "/api/database", "status": "ERROR"}]})
LOGS = json.dumps(
    [
        {"severity": "INFO", "message": "request received"},
        {"severity": "CRITICAL", "message": "ConnectionTimeoutError: db-primary:5432 after 10000ms"},
    ]
)


def _scripted(agent):
    """The agent with the scripted model, also when a GEMINI_API_KEY is set."""
    return mock.patch.object(agent, "model", simulated_llm.SimulatedLlm())


class TestSimulatedLlm(unittest.TestCase):
    def test_picks_the_trace_the_user_names_or_the_first(self) -> None:
        request = f"Find the failing trace ID in these traces:\n{json.dumps(CANDIDATES)}"
        self.assertEqual(simulated_llm._pick_trace(request), ERR)
        self.assertEqual(simulated_llm._pick_trace(f"The user asked: why is {SLOW} slow?\n\n{request}"), SLOW)


class TestLogCorrelator(unittest.IsolatedAsyncioTestCase):
    """Step 2: the LogCorrelator is an ADK agent with an instruction and the SRE tools."""

    async def _run(self) -> tuple[list[str], str]:
        """Runs the agent alone on one trace. Returns the tools it called and its answer."""
        # A copy: running an agent on its own changes it to chat mode, which the workflow refuses.
        agent = sre_workflow.log_correlator.model_copy(update={"model": simulated_llm.SimulatedLlm()})
        sessions = InMemorySessionService()
        runner = Runner(agent=agent, app_name="test", session_service=sessions)
        session = await sessions.create_session(app_name="test", user_id="u")
        prompt = f"Trace Spans:\n{SPANS}\n\nCorrelated Logs:\n{LOGS}\n\nProvide a root cause analysis."
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        calls, answer = [], ""
        async for event in runner.run_async(user_id="u", session_id=session.id, new_message=message):
            calls += [call.name for call in event.get_function_calls()]
            answer += "".join(p.text or "" for p in (event.content.parts if event.content else []))
        return calls, answer

    def test_it_has_the_sre_tools(self) -> None:
        names = {getattr(tool, "__name__", getattr(tool, "name", None)) for tool in sre_workflow.log_correlator.tools}
        self.assertLessEqual(
            {"query_metrics", "list_metric_descriptors", "analyze_trace_cascade", "generate_post_mortem"}, names
        )
        self.assertEqual(sre_workflow.log_correlator.instruction, sre_workflow.LOG_CORRELATOR_INSTRUCTION)

    async def test_it_checks_the_metrics_before_it_answers(self) -> None:
        calls, answer = await self._run()
        self.assertEqual(calls, ["query_metrics", "query_metrics"])
        self.assertIn("ConnectionTimeoutError", answer)
        self.assertNotIn("Not checked", answer)


class TestWorkflow(unittest.IsolatedAsyncioTestCase):
    """Step 1: the workflow runs TraceAnalyzer -> fetch_telemetry -> LogCorrelator."""

    async def _diagnose(self, question: str = "") -> sre_workflow.Diagnosis:
        telemetry = {
            "get_trace_details": mock.AsyncMock(return_value=SPANS),
            "query_logs_by_trace": mock.AsyncMock(return_value=LOGS),
            "analyze_trace_cascade": mock.AsyncMock(return_value="CASCADE"),
            "generate_post_mortem": mock.AsyncMock(return_value="POST-MORTEM"),
        }
        with (
            _scripted(sre_workflow.trace_analyzer),
            _scripted(sre_workflow.log_correlator),
            mock.patch.multiple(sre_workflow, **telemetry),
            mock.patch("sre_agent.inventory_client.fetch_topology", mock.AsyncMock(return_value={})),
        ):
            return await sre_workflow._run_adk_diagnostics(json.dumps(CANDIDATES), "demo", CANDIDATES[0], question)

    async def test_both_agents_run_on_the_picked_trace(self) -> None:
        diagnosis = await self._diagnose()
        self.assertFalse(diagnosis.failed, diagnosis.report)
        self.assertEqual(diagnosis.trace_id, ERR)
        # The LogCorrelator's answer is built from the logs that fetch_telemetry passed to it.
        self.assertIn("Root Cause Analysis", diagnosis.report)
        self.assertIn("ConnectionTimeoutError", diagnosis.report)
        self.assertTrue(diagnosis.report.rstrip().endswith("CASCADE\n\nPOST-MORTEM"))

    async def test_the_trace_analyzer_picks_the_trace_the_user_asks_about(self) -> None:
        self.assertEqual((await self._diagnose(f"why is {SLOW} slow?")).trace_id, SLOW)


if __name__ == "__main__":
    unittest.main()
