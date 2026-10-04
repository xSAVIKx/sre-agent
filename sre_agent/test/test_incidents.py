"""Which request gets diagnosed, and what its post-mortem says.

Found on the demo: asked "what are the latest failures?", the agent diagnosed its
own 44 s chat request - the slowest trace in the project, which had not failed -
and wrote a post-mortem about a database timeout that never happened.
"""

import json
import unittest
from typing import ClassVar
from unittest import mock

from sre_agent import gcp_tools, incidents


def _trace(trace_id: str, *, service: str = "sre-chaos-monkey", name: str = "/api/gateway", **kw) -> dict:
    return {"traceId": trace_id, "service": service, "name": name, "startTime": "2026-10-04T10:00:00Z", **kw}


class TestRanking(unittest.TestCase):
    def test_failures_outrank_slowness_and_agents_are_ignored(self) -> None:
        traces = [
            _trace("own-chat", service="sre-agent", name="/chat", durationMs=44818),
            _trace("slow", durationMs=9000),
            _trace("old-error", error=True, startTime="2026-10-04T09:00:00Z"),
            _trace("new-error", error=True, startTime="2026-10-04T11:00:00Z"),
            _trace("fine", durationMs=120),
            _trace("probe", name="/health", error=True),
        ]
        ranked = incidents.rank_incidents(traces)
        self.assertEqual([t["traceId"] for t in ranked], ["new-error", "old-error", "slow"])
        self.assertEqual([t["incident"] for t in ranked], ["error", "error", "slow"])

    def test_slow_requests_rank_by_duration(self) -> None:
        ranked = incidents.rank_incidents([_trace("a", durationMs=6000), _trace("b", durationMs=12000)])
        self.assertEqual([t["traceId"] for t in ranked], ["b", "a"])


class TestFindIncident(unittest.IsolatedAsyncioTestCase):
    async def test_best_candidate_wins(self) -> None:
        traces = [_trace("own-chat", service="sre-sub-agent", durationMs=60000), _trace("err", error=True)]
        incident, candidates = await incidents.find_incident(json.dumps(traces))
        self.assertEqual(incident["traceId"], "err")
        self.assertEqual(len(candidates), 1)

    async def test_error_logs_point_at_the_trace_when_no_trace_looks_bad(self) -> None:
        logs = [
            {"severity": "INFO", "text_payload": "ok", "trace": "projects/p/traces/aaa"},
            {"severity": "ERROR", "text_payload": "boom", "trace": "projects/p/traces/bbb"},
        ]
        with mock.patch.object(gcp_tools, "query_logs", mock.AsyncMock(return_value=json.dumps(logs))):
            incident, _ = await incidents.find_incident(json.dumps([_trace("fine", durationMs=100)]))
        self.assertEqual(incident["traceId"], "bbb")
        self.assertEqual(incident["incident"], "error")

    async def test_healthy_when_nothing_is_wrong(self) -> None:
        with mock.patch.object(gcp_tools, "query_logs", mock.AsyncMock(return_value="[]")):
            incident, candidates = await incidents.find_incident(json.dumps([_trace("fine", durationMs=100)]))
        self.assertIsNone(incident)
        self.assertEqual(candidates, [])


class TestServiceName(unittest.TestCase):
    def test_from_revision_and_hosts(self) -> None:
        f = gcp_tools._service_name
        revision = "//run.googleapis.com/projects/p/locations/us-central1/revisions/sre-agent-00069-gm7"
        self.assertEqual(f({"labels": {"cloud.resource_id": revision}}), "sre-agent")
        self.assertEqual(
            f({"labels": {"/http/host": "sre-sub-agent-285931116611.us-central1.run.app"}}), "sre-sub-agent"
        )
        self.assertEqual(f({"labels": {"/http/host": "inventory-agent-oeglp6ptnq-uc.a.run.app"}}), "inventory-agent")
        self.assertEqual(f({"labels": {}}), "")


class TestPostMortem(unittest.TestCase):
    SLOW: ClassVar[dict] = {
        "root_span": "/chat",
        "durationMs": 44818,
        "spans": [
            {
                "name": "/chat",
                "spanId": "1",
                "startTime": "2026-10-04T14:36:31.940Z",
                "endTime": "2026-10-04T14:37:16.758Z",
            },
            {
                "name": "POST sre-sub-agent",
                "spanId": "2",
                "parentSpanId": "1",
                "startTime": "2026-10-04T14:36:38.433Z",
                "endTime": "2026-10-04T14:37:16.521Z",
            },
        ],
    }

    def test_slow_request_is_not_described_as_a_failure(self) -> None:
        report = gcp_tools._render_post_mortem("t" * 32, self.SLOW, [])
        self.assertIn("# 🚨 Incident Post-Mortem", report)
        self.assertIn("slow, not failing", report)
        self.assertIn("`POST sre-sub-agent` - 38088 ms of its own (85.0% of the request)", report)
        for claim in ("database", "ConnectionTimeoutError", "RESOLVED` (", "chaos"):
            self.assertNotIn(claim, report)
        self.assertIn("**Status**: `OPEN`", report)

    def test_timeline_follows_real_time_across_timestamp_formats(self) -> None:
        logs = [{"severity": "ERROR", "text_payload": "late", "timestamp": "2026-10-04T16:37:20+02:00"}]
        report = gcp_tools._render_post_mortem("t" * 32, self.SLOW, logs)
        timeline = report.split("## 🔍 Incident Timeline")[1].split("##")[0]
        self.assertLess(timeline.index("received the request"), timeline.index("late"))
        self.assertLess(timeline.index("Request finished"), timeline.index("late"))


if __name__ == "__main__":
    unittest.main()
