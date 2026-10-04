"""Tests for the incident severity badge (workshop step 5)."""

import unittest

from agent.a2ui_translator import classify_severity, translate_markdown_to_a2ui


def _report(contribution: float) -> str:
    return f"# 🚨 Incident Post-Mortem\n\n*   **Self-Execution Time**: `10200 ms` ({contribution}% of total trace)\n"


class TestSeverityBadge(unittest.TestCase):
    def test_levels_follow_the_bottleneck_share(self) -> None:
        self.assertEqual(classify_severity(_report(99.3))["level"], "SEV1")
        self.assertEqual(classify_severity(_report(90.0))["level"], "SEV1")
        self.assertEqual(classify_severity(_report(64.0))["level"], "SEV2")
        self.assertEqual(classify_severity(_report(12.5))["level"], "SEV3")

    def test_badge_carries_the_contribution(self) -> None:
        self.assertEqual(
            classify_severity(_report(99.3)), {"type": "severity_badge", "level": "SEV1", "contribution": 99.3}
        )

    def test_no_bottleneck_no_badge(self) -> None:
        self.assertIsNone(classify_severity("Diagnostics completed. All systems are healthy."))

    def test_post_mortem_leads_with_the_badge(self) -> None:
        components = translate_markdown_to_a2ui(_report(99.3))["components"]
        self.assertEqual(components[0]["type"], "severity_badge")
        self.assertIn("download_button", [c["type"] for c in components])


if __name__ == "__main__":
    unittest.main()
