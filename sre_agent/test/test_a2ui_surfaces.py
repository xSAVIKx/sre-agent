"""The A2UI surfaces of the SRE agent's results, checked by A2UI's own message processor.

`a2ui-core`'s MessageProcessor is the reference implementation of an A2UI renderer's
state handling. With strict validation it rejects unknown components, wrong property
types, dangling child references and orphaned components - so a surface that passes
here is one the browser's renderer will accept.
"""

import unittest

from a2ui.core.processing.message_processor import MessageProcessor, MessageProcessorOptions
from a2ui.core.validation import STRICT_VALIDATION

from sre_agent import a2ui_surfaces

TRACE = "1c65bf87e4be434ea6d6d7edc1ef8c97"
INCIDENTS = [
    {"traceId": TRACE, "incident": "error", "service": "sre-chaos-monkey", "name": "/api/gateway",
     "durationMs": 10442, "startTime": "2026-10-04T17:41:24.123Z"},
    {"traceId": "b" * 32, "incident": "slow", "service": "sre-chaos-monkey", "name": "/api/gateway",
     "durationMs": 9000, "startTime": "2026-10-04T17:40:00Z"},
]  # fmt: skip
POST_MORTEM = "# 🚨 Incident Post-Mortem\n\n## 📝 Incident Overview\n*   **Trace ID**: `x`"
DIAGNOSIS = (
    "### Root cause\nThe database timed out.\n\n"
    "## ⛓️ Multi-Service Cascade Latency & Bottleneck Analysis\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    f"{POST_MORTEM}"
)


def _render(messages: list[dict]) -> MessageProcessor:
    processor = MessageProcessor(
        [a2ui_surfaces.sre_catalog()], options=MessageProcessorOptions(validation_config=STRICT_VALIDATION)
    )
    processor.process_messages(messages)
    return processor


def _components(messages: list[dict]) -> dict[str, dict]:
    (update,) = [m["updateComponents"] for m in messages if "updateComponents" in m]
    return {c["id"]: c for c in update["components"]}


class TestSurfacesAreValidA2ui(unittest.TestCase):
    def test_every_surface_passes_strict_validation(self) -> None:
        surfaces = {
            "incidents": a2ui_surfaces.incident_list_surface("demo", INCIDENTS),
            "no incidents": a2ui_surfaces.incident_list_surface("demo", []),
            "diagnosis": a2ui_surfaces.diagnosis_surface(DIAGNOSIS, TRACE, 97.5),
            "healthy diagnosis": a2ui_surfaces.diagnosis_surface("All systems are healthy.", None, None),
            "post-mortem": a2ui_surfaces.post_mortem_surface(POST_MORTEM, TRACE, 64.0),
            "nothing to write up": a2ui_surfaces.post_mortem_surface("Nothing.", None, None),
        }
        for name, messages in surfaces.items():
            with self.subTest(name):
                self.assertEqual(messages[0]["createSurface"]["catalogId"], a2ui_surfaces.SRE_CATALOG_ID)
                _render(messages)  # raises on any invalid message

    def test_each_result_gets_its_own_surface(self) -> None:
        first, second = (a2ui_surfaces.incident_list_surface("demo", []) for _ in range(2))
        self.assertNotEqual(first[0]["createSurface"]["surfaceId"], second[0]["createSurface"]["surfaceId"])


class TestIncidentList(unittest.TestCase):
    def test_rows_come_from_the_data_model(self) -> None:
        messages = a2ui_surfaces.incident_list_surface("demo", INCIDENTS)
        components = _components(messages)
        self.assertEqual(components["incidents"]["children"], {"componentId": "row", "path": "/incidents"})

        rows = messages[2]["updateDataModel"]["value"]["incidents"]
        self.assertEqual([r["traceId"] for r in rows], [TRACE, "b" * 32])
        self.assertEqual(rows[0]["summary"], "❌ **failing** `/api/gateway` on `sre-chaos-monkey`")
        self.assertEqual(rows[0]["details"], f"10442 ms · 2026-10-04 17:41:24 UTC · trace {TRACE}")

    def test_buttons_send_the_rows_trace_back(self) -> None:
        components = _components(a2ui_surfaces.incident_list_surface("demo", INCIDENTS))
        for button, action in (("diagnose", "diagnose_incident"), ("post-mortem", "write_post_mortem")):
            self.assertEqual(
                components[button]["action"],
                {"event": {"name": action, "context": {"traceId": {"path": "traceId"}}}},
            )


class TestDiagnosis(unittest.TestCase):
    def test_report_sections_become_tabs(self) -> None:
        components = _components(a2ui_surfaces.diagnosis_surface(DIAGNOSIS, TRACE, 97.5))
        self.assertEqual(
            [t["title"] for t in components["sections"]["tabs"]], ["Analysis", "Bottleneck", "Post-mortem"]
        )
        self.assertTrue(components["tab-1"]["text"].startswith("## ⛓️ Multi-Service Cascade"))
        self.assertEqual(components["download"]["content"], DIAGNOSIS)
        self.assertEqual(components["post-mortem"]["action"]["event"]["context"], {"traceId": TRACE})


class TestPostMortem(unittest.TestCase):
    def test_analyst_notes_get_their_own_tab(self) -> None:
        notes = "**Likely causes**\n- the database"
        report = f"{POST_MORTEM}\n\n{a2ui_surfaces.ANALYSIS_HEADING}\n\n{notes}"
        components = _components(a2ui_surfaces.post_mortem_surface(report, TRACE, 64.0))
        self.assertEqual([t["title"] for t in components["sections"]["tabs"]], ["Post-mortem", "Analyst notes (AI)"])
        self.assertEqual(components["tab-1"]["text"], notes)
        self.assertNotIn("Incident Post-Mortem", components["tab-0"]["text"], "the card title replaces the H1")


class TestNegotiation(unittest.TestCase):
    def test_only_clients_listing_the_sre_catalog_get_surfaces(self) -> None:
        caps = a2ui_surfaces.CLIENT_CAPABILITIES_KEY
        self.assertTrue(a2ui_surfaces.client_renders_sre_catalog({caps: a2ui_surfaces.client_capabilities()}))
        basic_only = {caps: {"v0.9": {"supportedCatalogIds": [a2ui_surfaces.BASIC_CATALOG_ID]}}}
        self.assertFalse(a2ui_surfaces.client_renders_sre_catalog(basic_only))
        self.assertFalse(a2ui_surfaces.client_renders_sre_catalog({}))


if __name__ == "__main__":
    unittest.main()
