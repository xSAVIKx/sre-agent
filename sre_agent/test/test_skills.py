"""The SRE agent's skills: what each one returns, and what reaches the ADK workflow."""

import json
import unittest
from unittest import mock

from sre_agent.diagnosis import Progress, Report

from sre_agent import diagnosis, post_mortem_analysis, sre_workflow

TRACES = [
    {"traceId": "own", "service": "sre-agent", "name": "/chat", "startTime": "2026-10-04T10:05:00Z", "durationMs": 40000},
    {"traceId": "slow", "service": "sre-chaos-monkey", "name": "/api/gateway", "startTime": "2026-10-04T10:01:00Z", "durationMs": 9000},
    {"traceId": "err", "service": "sre-chaos-monkey", "name": "/api/gateway", "startTime": "2026-10-04T10:00:00Z", "durationMs": 300, "error": True},
    {"traceId": "fine", "service": "sre-chaos-monkey", "name": "/api/gateway", "startTime": "2026-10-04T10:02:00Z", "durationMs": 80},
]  # fmt: skip


async def _collect(gen) -> tuple[list[str], Report]:
    updates = [u async for u in gen]
    *progress, report = updates
    assert all(isinstance(p, Progress) for p in progress) and isinstance(report, Report)
    return [p.text for p in progress], report


class TestListIncidents(unittest.IsolatedAsyncioTestCase):
    async def test_ranked_table_and_data_without_the_agents_own_traffic(self) -> None:
        with mock.patch.object(diagnosis, "query_traces", mock.AsyncMock(return_value=json.dumps(TRACES))):
            _, report = await _collect(diagnosis.run_list_incidents(project_id="demo"))

        self.assertEqual([i["traceId"] for i in report.data["incidents"]], ["err", "slow"])
        self.assertEqual(report.data["kind"], "incident_list")
        self.assertIn("1 failing and 1 slow", report.text)
        self.assertIn("| 1 | ❌ failing | `sre-chaos-monkey` | /api/gateway | 300 ms |", report.text)
        self.assertNotIn("`own`", report.text)

    async def test_healthy_project(self) -> None:
        healthy = json.dumps(TRACES[3:])
        with (
            mock.patch.object(diagnosis, "query_traces", mock.AsyncMock(return_value=healthy)),
            mock.patch("sre_agent.gcp_tools.query_logs", mock.AsyncMock(return_value="[]")),
        ):
            _, report = await _collect(diagnosis.run_list_incidents(project_id="demo"))
        self.assertIn("No recent incidents", report.text)
        self.assertEqual(report.data["incidents"], [])


class TestPostMortem(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.generate = mock.AsyncMock(return_value="# 🚨 Incident Post-Mortem\nevidence")
        patcher = mock.patch.object(diagnosis, "generate_post_mortem", self.generate)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_template_only_without_a_key(self) -> None:
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            progress, report = await _collect(diagnosis.run_post_mortem(project_id="demo", trace_id="t1"))
        self.generate.assert_awaited_once_with("t1", "demo")
        self.assertEqual(report.text, "# 🚨 Incident Post-Mortem\nevidence")
        self.assertEqual(
            report.data, {"kind": "post_mortem", "project_id": "demo", "trace_id": "t1", "llm_analysis": False}
        )
        self.assertFalse(any("Gemini" in p for p in progress))

    async def test_analyst_notes_appended_with_a_key(self) -> None:
        notes = f"{post_mortem_analysis.ANALYSIS_HEADING}\n\n**Likely causes**\n- x"
        analyze = mock.AsyncMock(return_value=notes)
        with (
            mock.patch.object(diagnosis, "analysis_enabled", return_value=True),
            mock.patch.object(diagnosis, "analyze_post_mortem", analyze),
        ):
            _, report = await _collect(diagnosis.run_post_mortem("why?", project_id="demo", trace_id="t1"))
        analyze.assert_awaited_once_with("# 🚨 Incident Post-Mortem\nevidence", "why?")
        self.assertTrue(report.text.endswith(notes))
        self.assertTrue(report.data["llm_analysis"])

    async def test_defaults_to_the_most_important_incident(self) -> None:
        with mock.patch.object(diagnosis, "query_traces", mock.AsyncMock(return_value=json.dumps(TRACES))):
            _, report = await _collect(diagnosis.run_post_mortem(project_id="demo"))
        self.generate.assert_awaited_once_with("err", "demo")
        self.assertEqual(report.data["trace_id"], "err")

    async def test_failed_analysis_leaves_the_template(self) -> None:
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}),
            mock.patch("google.adk.runners.Runner", side_effect=RuntimeError("quota")),
        ):
            self.assertEqual(await post_mortem_analysis.analyze_post_mortem("pm"), "")


class TestDiagnosisInputs(unittest.IsolatedAsyncioTestCase):
    async def test_requested_trace_is_diagnosed(self) -> None:
        simulated = mock.AsyncMock(return_value="report")
        with (
            mock.patch.object(sre_workflow, "_run_simulated_diagnostics", simulated),
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
        ):
            await sre_workflow.run_sre_diagnostics(json.dumps(TRACES), "demo", trace_id="slow")
        incident = simulated.await_args.args[0]
        self.assertEqual((incident["traceId"], incident["durationMs"]), ("slow", 9000))

    async def test_question_and_candidates_reach_the_adk_workflow(self) -> None:
        adk = mock.AsyncMock(return_value=sre_workflow.Diagnosis("report", "err"))
        with (
            mock.patch.object(sre_workflow, "_run_adk_diagnostics", adk),
            mock.patch.object(sre_workflow, "HAS_ADK", True),
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}),
        ):
            await sre_workflow.run_sre_diagnostics(json.dumps(TRACES), "demo", question="why is it slow?")
        candidates, project, incident, question = adk.await_args.args
        self.assertEqual([c["traceId"] for c in json.loads(candidates)], ["err", "slow"])
        self.assertEqual((project, incident["traceId"], question), ("demo", "err", "why is it slow?"))


if __name__ == "__main__":
    unittest.main()
