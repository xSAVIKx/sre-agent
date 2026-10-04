"""Chat sessions: the browser must learn the conversation ID, and transcripts must persist.

Two regressions this pins (both found on the deployed demo):
- With the real Antigravity SDK a new conversation has no ID until its first turn
  has run, so the "start" event carried null and every follow-up started a new
  conversation without memory. The ID now also travels in the "done" event.
- The raw SDK steps of one diagnosis (~90 streamed steps, each with a copy of the
  A2UI payload) exceeded Firestore's 1 MiB document limit and the write failed
  silently. The transcript is now one compact user/model pair per turn.
"""

import json
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import config, routes


def _events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class _Chunk:
    def __init__(self, cls_name: str, **fields) -> None:
        self.__class__ = type(cls_name, (), {})
        self.__dict__.update(fields)


class _SdkLikeAgent:
    """Mimics the real SDK: conversation_id stays None until the turn's chunks are consumed."""

    def __init__(self, config) -> None:
        self.conversation_id = config.conversation_id
        self.conversation = mock.Mock(history=[])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def chat(self, prompt: str):
        agent = self

        class _Response:
            @property
            def chunks(self):
                async def gen():
                    yield _Chunk("Text", text=f"reply to {prompt}")
                    agent.conversation_id = agent.conversation_id or "conv-123"

                return gen()

            async def cancel(self) -> None:
                return None

        return _Response()


class TestChatSessions(unittest.TestCase):
    def setUp(self) -> None:
        if config.HAS_ANTIGRAVITY:
            self.skipTest("Exercises the simulation-mode storage; unset GEMINI_API_KEY to run.")
        config.MOCK_HISTORY_DB.clear()
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def _chat(self, prompt: str, conversation_id: str | None = None) -> list[dict]:
        resp = self.client.post("/chat", json={"prompt": prompt, "conversation_id": conversation_id})
        self.assertEqual(resp.status_code, 200)
        return _events(resp.text)

    def test_done_carries_an_id_that_was_unknown_at_start(self) -> None:
        with mock.patch.object(routes, "Agent", _SdkLikeAgent):
            events = self._chat("hello")
        self.assertIsNone(events[0]["conversation_id"], "the SDK has no ID yet when the turn starts")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["conversation_id"], "conv-123")
        self.assertEqual(len(config.MOCK_HISTORY_DB["conv-123"]), 2, "the turn is saved under the late ID")

    def test_follow_ups_append_compact_turns(self) -> None:
        report = "# 🚨 Incident Post-Mortem\n\nRoot trace: abc123"

        async def fake_diagnose(prompt, project_id=None, refresh=False):
            config.diagnosis_sink.get().report = report
            return report

        with mock.patch.object(config, "diagnose_sre", fake_diagnose):
            first = self._chat("Diagnose the latency spike")
            conv_id = first[-1]["conversation_id"]
            self._chat("hello again", conversation_id=conv_id)

        history = self.client.get(f"/sessions/{conv_id}/history").json()["history"]
        self.assertEqual([e["source"] for e in history], ["USER", "MODEL", "USER", "MODEL"])
        self.assertEqual(history[0]["content"], "Diagnose the latency spike")
        self.assertEqual(history[1]["tool_calls"], [{"name": "diagnose_sre"}])
        # The A2UI payload is rebuilt on read, not stored.
        stored = config.MOCK_HISTORY_DB[conv_id][1]
        self.assertNotIn("response_a2ui", stored)
        self.assertIn("download_button", [c["type"] for c in history[1]["response_a2ui"]["components"]])

    def test_turn_entries_keep_rendered_only_when_it_differs(self) -> None:
        _, same = routes._turn_entries("p", "reply", [], [], "reply")
        _, different = routes._turn_entries("p", "short summary", ["step"], ["diagnose_sre"], "full report")
        self.assertNotIn("rendered", same)
        self.assertEqual(different["rendered"], "full report")
        self.assertEqual(different["thinking"], "step")


if __name__ == "__main__":
    unittest.main()
