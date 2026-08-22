"""The committed telemetry fixtures must stay in step with the app that writes them.

`sre_agent/test/fixtures/` holds one incident's worth of mock telemetry, and
`sre_agent/test/test_gcp_tools.py` drives its whole trace-cascade and post-mortem
suite off it. Those fixtures are only worth anything if they are what
`app/main.py` actually produces - otherwise the tests describe an incident the
demo cannot generate, and the post-mortem a reader gets from
`simulate_incident.py` disagrees with the one the tests assert on.

That had already happened. The fixture's final log line read

    Gateway received error from backend: Internal Server Error

but `app/main.py` logs `e.detail` of the *incoming* HTTPException, which is the
database's own message; `Internal Server Error` is the detail of the *outgoing*
one raised on the next line. `generate_post_mortem` renders that line verbatim
into "Incident Timeline" step 2, so the drift was user-visible. Nothing caught
it - deleting the entry outright left all twenty `sre_agent` tests green.

So drive the app the way `simulate_incident.py` does, and diff. Everything is
reproducible except the trace ID and the log timestamps, which `_log_structured`
stamps from the wall clock; the committed fixture rewrites those to describe the
ten-second incident the trace spans encode, so they are compared for presence
rather than value.
"""

import asyncio
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "sre_agent" / "test" / "fixtures"
FIXTURE_TRACE_ID = "06f96234b89348488f6a2a01b1fc4632"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read_json(path: pathlib.Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class TestTelemetryFixtures(unittest.TestCase):
    """Regenerates the incident from `app/main.py` and diffs it against the fixtures."""

    def setUp(self) -> None:
        """Runs the chaos-monkey gateway once, in mock mode, into a throwaway directory."""
        from fastapi import HTTPException

        from app import main as chaos_monkey

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mock_dir = pathlib.Path(tmp.name)

        with (
            mock.patch.object(chaos_monkey, "IS_MOCK", True),
            mock.patch.object(chaos_monkey, "MOCK_DATA_DIR", str(self.mock_dir)),
            # `_log_structured` also prints every entry; six JSON blobs per test
            # would bury the CI test output for no benefit.
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(HTTPException) as raised,
        ):
            # The same call `simulate_incident.py` makes: one gateway request with
            # the failure injected, which cascades through backend and database.
            asyncio.run(chaos_monkey.gateway(mock.Mock(), trigger_error=True))

        # The gateway re-raises with `detail={"error": ..., "trace_id": ...}`.
        self.trace_id: str = raised.exception.detail["trace_id"]

    def _generated(self, kind: str) -> Any:
        """Reads a freshly written telemetry file with the fixture's trace ID substituted in."""
        raw = (self.mock_dir / f"{kind}_{self.trace_id}.json").read_text(encoding="utf-8")
        return json.loads(raw.replace(self.trace_id, FIXTURE_TRACE_ID))

    def test_trace_fixture_matches_what_the_app_writes(self) -> None:
        """The trace file is fully deterministic, so it must match field for field."""
        self.assertEqual(
            _read_json(FIXTURE_DIR / f"trace_{FIXTURE_TRACE_ID}.json"),
            self._generated("trace"),
            "sre_agent/test/fixtures/trace_*.json has drifted from app/main.py:_generate_mock_trace",
        )

    def test_log_fixture_matches_what_the_app_writes(self) -> None:
        """Same for the logs, minus the wall-clock timestamps."""
        committed = _read_json(FIXTURE_DIR / f"logs_{FIXTURE_TRACE_ID}.json")
        generated = self._generated("logs")

        self.assertEqual(
            [{k: v for k, v in entry.items() if k != "timestamp"} for entry in generated],
            [{k: v for k, v in entry.items() if k != "timestamp"} for entry in committed],
            "sre_agent/test/fixtures/logs_*.json has drifted from app/main.py:_log_structured",
        )
        for entry in committed:
            self.assertRegex(entry["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_the_gateway_logs_the_databases_error_not_its_own(self) -> None:
        """Pins the line the post-mortem renders verbatim into its timeline.

        `app/main.py` logs the *incoming* exception's detail here. If that ever
        becomes the outgoing `Internal Server Error` again, the fixture, the
        tests and a live `simulate_incident.py` run all quietly disagree.
        """
        self.assertEqual(
            self._generated("logs")[-1]["message"],
            "Gateway received error from backend: ConnectionTimeoutError: "
            "Failed to connect to db-primary.gcp.internal:5432 after 10000ms",
        )


if __name__ == "__main__":
    unittest.main()
