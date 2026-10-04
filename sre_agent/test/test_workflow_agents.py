"""Tests for the ADK agents in the SRE diagnostics workflow (workshop step 3)."""

import unittest

from sre_agent import sre_workflow


def _tool_names(agent: object) -> set[str]:
    return {getattr(tool, "__name__", getattr(tool, "name", str(tool))) for tool in getattr(agent, "tools", [])}


class TestLogCorrelatorToolbelt(unittest.TestCase):
    def test_log_correlator_can_measure_and_write_up(self) -> None:
        """The LogCorrelator needs metrics, cascade analysis and the post-mortem writer."""
        self.assertTrue(
            {"query_metrics", "analyze_trace_cascade", "generate_post_mortem"}
            <= _tool_names(sre_workflow.log_correlator)
        )

    def test_trace_analyzer_stays_tool_free(self) -> None:
        """The TraceAnalyzer only picks a trace ID from data it is handed."""
        self.assertEqual(_tool_names(sre_workflow.trace_analyzer), set())


if __name__ == "__main__":
    unittest.main()
