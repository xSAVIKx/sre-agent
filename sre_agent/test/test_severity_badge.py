"""Tests for the incident severity badge (workshop step 5)."""

import unittest

from sre_agent.a2ui_surfaces import classify_severity

from sre_agent import a2ui_surfaces


class TestSeverityBadge(unittest.TestCase):
    def test_levels_follow_the_bottleneck_share(self) -> None:
        self.assertEqual(classify_severity(99.3), "SEV1")
        self.assertEqual(classify_severity(90.0), "SEV1")
        self.assertEqual(classify_severity(64.0), "SEV2")
        self.assertEqual(classify_severity(12.5), "SEV3")
        self.assertEqual(classify_severity(0.0), "SEV3")

    def test_post_mortem_surface_leads_with_the_badge(self) -> None:
        messages = a2ui_surfaces.post_mortem_surface("# 🚨 Incident Post-Mortem\n\nbody", "t" * 32, 99.3)
        components = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}
        self.assertEqual(components["body"]["children"][0], "severity")
        self.assertEqual(
            components["severity"],
            {"id": "severity", "component": "SeverityBadge", "level": "SEV1", "contribution": 99.3},
        )

    def test_no_bottleneck_no_badge(self) -> None:
        messages = a2ui_surfaces.post_mortem_surface("# 🚨 Incident Post-Mortem\n\nbody", "t" * 32, None)
        self.assertNotIn("SeverityBadge", [c["component"] for c in messages[1]["updateComponents"]["components"]])


if __name__ == "__main__":
    unittest.main()
