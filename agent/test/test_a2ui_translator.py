"""Unit tests for the A2UI translator."""

import unittest
from agent.a2ui_translator import translate_markdown_to_a2ui


class TestA2uiTranslator(unittest.TestCase):
    """Tests for parsing markdown agent reports to A2UI component schemas."""

    def test_translate_generic_text(self) -> None:
        """Verifies simple text strings are wrapped in a text component."""
        text = "Hello! I am ready to help you with SRE tasks."
        a2ui = translate_markdown_to_a2ui(text)
        
        self.assertEqual(a2ui["type"], "container")
        self.assertEqual(len(a2ui["components"]), 1)
        self.assertEqual(a2ui["components"][0]["type"], "text")
        self.assertEqual(a2ui["components"][0]["content"], text)

    def test_translate_already_json(self) -> None:
        """Verifies valid A2UI JSON output from agent is returned directly as a dict."""
        json_payload = '{"type": "container", "components": [{"type": "header", "text": "Direct JSON"}]}'
        a2ui = translate_markdown_to_a2ui(json_payload)
        
        self.assertEqual(a2ui["type"], "container")
        self.assertEqual(a2ui["components"][0]["text"], "Direct JSON")

    def test_translate_sre_incident_report(self) -> None:
        """Verifies that markdown SRE diagnostics reports are parsed into rich component grids."""
        report = (
            "# 🚨 SRE Incident Diagnosis Report\n\n"
            "**Anomalous Trace ID**: `abc123traceid`\n"
            "**Root Service**: `/api/gateway`\n\n"
            "## 🔍 Root Cause Analysis\n"
            "We found that database timeout occurred due to connection pool exhaustion.\n\n"
            "```\n"
            "TimeoutError: Connection failed\n"
            "```\n\n"
            "## 🛠️ Recommended Mitigation\n"
            "1. **Check Database Health**: Run health checks on PostgreSQL.\n"
            "2. **Adjust Connection Pools**: Scale up pool limits."
        )

        a2ui = translate_markdown_to_a2ui(report)

        self.assertEqual(a2ui["type"], "container")
        components = a2ui["components"]
        self.assertEqual(len(components), 5)

        # 1. Alert component
        self.assertEqual(components[0]["type"], "alert")
        self.assertEqual(components[0]["level"], "error")
        self.assertEqual(components[0]["title"], "Incident Detected")
        self.assertIn("abc123traceid", components[0]["text"])
        self.assertIn("/api/gateway", components[0]["text"])

        # 2. Metadata Card
        self.assertEqual(components[1]["type"], "card")
        self.assertEqual(components[1]["title"], "Incident Metadata")
        fields = components[1]["fields"]
        self.assertEqual(fields[0]["label"], "Anomalous Trace ID")
        self.assertEqual(fields[0]["value"], "abc123traceid")
        self.assertEqual(fields[1]["label"], "Root Service")
        self.assertEqual(fields[1]["value"], "/api/gateway")

        # 3. Root Cause Section
        self.assertEqual(components[2]["type"], "section")
        self.assertEqual(components[2]["title"], "🔍 Root Cause Analysis")
        self.assertEqual(
            components[2]["content"],
            "We found that database timeout occurred due to connection pool exhaustion."
        )

        # 4. Logs Block
        self.assertEqual(components[3]["type"], "code_block")
        self.assertEqual(components[3]["title"], "Correlated Diagnostics Logs")
        self.assertEqual(components[3]["code"], "TimeoutError: Connection failed")

        # 5. Mitigation List
        self.assertEqual(components[4]["type"], "list")
        self.assertEqual(components[4]["title"], "🛠️ Recommended Mitigations")
        items = components[4]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], "**Check Database Health**: Run health checks on PostgreSQL.")
        self.assertEqual(items[1], "**Adjust Connection Pools**: Scale up pool limits.")

    def test_translate_healthy_diagnostics_report(self) -> None:
        """Verifies that a clean system health report maps to a success alert."""
        text = "Diagnostics completed. No anomalous traces or errors detected in the recent logs. All systems are healthy."
        a2ui = translate_markdown_to_a2ui(text)

        self.assertEqual(a2ui["type"], "container")
        components = a2ui["components"]
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["type"], "alert")
        self.assertEqual(components[0]["level"], "success")
        self.assertEqual(components[0]["title"], "Diagnostics Clean")
        self.assertEqual(components[0]["text"], text)


if __name__ == "__main__":
    unittest.main()
