"""A2UI (v0.9) surfaces for the SRE agent's results.

Each skill returns Markdown for models and terminals. When the caller can render
A2UI, it also returns the same result as an A2UI surface: a list of A2UI messages
(``createSurface``, ``updateComponents``, ``updateDataModel``) describing a UI out
of catalog components. The browser renders them with ``@a2ui/lit``; nothing in
this module knows about HTML.

* **Catalog.** Surfaces use the SRE catalog: A2UI's basic catalog (Card, Column,
  Row, List, Tabs, Text, Button, ...) plus two components of our own,
  `SeverityBadge` and `Download`. Their schemas are below; the browser
  implements them (``agent/web/src/sre-a2ui.js``).
* **Negotiation.** A caller that can render A2UI says so in the request metadata,
  as A2UI's client capabilities (`client_capabilities`). Without them the agent
  answers in Markdown only.
* **Transport.** Each message travels as an A2A data part whose metadata carries
  `A2UI_MIME_TYPE` (see `sre_agent.a2a_agent`).
"""

import functools
import re
import uuid
from typing import Any, Literal

from a2ui.core.basic_catalog.v0_9 import BasicCatalog
from a2ui.core.basic_catalog.v0_9.components import CatalogComponentCommon
from a2ui.core.catalog.catalog import Catalog
from a2ui.core.catalog.components import ModelComponentApi
from a2ui.core.schema.v0_9.common_types import DynamicNumber, DynamicString
from pydantic import Field

from sre_agent.post_mortem_analysis import ANALYSIS_HEADING

A2UI_VERSION = "v0.9"
# The A2A extension an agent advertises, and the media type of the data parts it sends.
A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"
A2UI_MIME_TYPE = "application/json+a2ui"
# Where a client lists the catalogs it can render (request metadata).
CLIENT_CAPABILITIES_KEY = "a2uiClientCapabilities"

BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
SRE_CATALOG_ID = "https://github.com/xSAVIKx/sre-agent/a2ui/catalogs/sre/v1/catalog.json"


class SeverityBadge(CatalogComponentCommon):
    """How bad an incident is: a SEV level and the bottleneck's share of the request."""

    component: Literal["SeverityBadge"] = "SeverityBadge"
    level: DynamicString = Field(..., description="SEV1 (worst), SEV2 or SEV3.")
    contribution: DynamicNumber = Field(..., description="The bottleneck span's share of the request, in percent.")


class Download(CatalogComponentCommon):
    """A button that saves text content as a file in the browser."""

    component: Literal["Download"] = "Download"
    label: DynamicString = Field(..., description="The button label.")
    filename: DynamicString = Field(..., description="The file name to save as.")
    content: DynamicString = Field(..., description="The file content.")


@functools.cache
def sre_catalog() -> Catalog:
    """The SRE catalog: the basic catalog's components and functions plus ours."""
    basic = BasicCatalog()
    return Catalog(
        SRE_CATALOG_ID,
        basic.protocol_version,
        [*basic.components.values(), ModelComponentApi(SeverityBadge), ModelComponentApi(Download)],
        functions=list(basic.functions.values()),
    )


def client_capabilities() -> dict[str, Any]:
    """The capabilities of a client that renders the SRE catalog, for request metadata."""
    return {A2UI_VERSION: {"supportedCatalogIds": [SRE_CATALOG_ID, BASIC_CATALOG_ID]}}


def client_renders_sre_catalog(metadata: dict[str, Any]) -> bool:
    """True when the request's A2UI client capabilities include the SRE catalog."""
    capabilities = metadata.get(CLIENT_CAPABILITIES_KEY) or {}
    catalogs = (capabilities.get(A2UI_VERSION) or {}).get("supportedCatalogIds") or []
    return SRE_CATALOG_ID in catalogs


# Bottleneck share of the whole request at or above which an incident gets each level.
SEVERITY_THRESHOLDS: tuple[tuple[float, str], ...] = ((90.0, "SEV1"), (50.0, "SEV2"), (0.0, "SEV3"))


def classify_severity(contribution: float) -> str:
    """The SEV level of an incident from its bottleneck's share of the request.

    The larger the share one span owns, the more clearly a single component is down,
    and the more severe the incident.

    Args:
        contribution: The bottleneck span's self time as a percentage of the request.

    Returns:
        "SEV1", "SEV2" or "SEV3".
    """
    return next(name for threshold, name in SEVERITY_THRESHOLDS if contribution >= threshold)


def _messages(components: list[dict[str, Any]], data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """A new surface: create it, define its components, and set its data model."""
    surface_id = f"sre-{uuid.uuid4().hex[:12]}"
    messages = [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": SRE_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
    ]
    if data is not None:
        messages.append(
            {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data}}
        )
    return messages


def _text(component_id: str, text: str | dict[str, str], variant: str = "body") -> dict[str, Any]:
    return {"id": component_id, "component": "Text", "text": text, "variant": variant}


_HEADING = re.compile(r"(?m)^(#{1,6}) ")


def _report_text(component_id: str, markdown: str) -> dict[str, Any]:
    """A report section as Markdown text, its top heading moved to h3.

    On the page the section sits under the card's h2 title, inside the chat's h1, so
    its headings must start at h3. Model-written sections may start at any level.
    """
    levels = [len(m.group(1)) for m in _HEADING.finditer(markdown)]
    shift = 3 - min(levels) if levels else 0
    return _text(component_id, _HEADING.sub(lambda m: "#" * min(len(m.group(1)) + shift, 6) + " ", markdown))


def _button(component_id: str, label: str, action: str, trace_id: str | dict[str, str]) -> list[dict[str, Any]]:
    """A button that sends `action` with the trace ID back to the agent, and its label."""
    return [
        {
            "id": component_id,
            "component": "Button",
            "child": f"{component_id}-label",
            # TODO(step-5): The click must tell the agent which trace: add a "context" with
            #   {"traceId": trace_id} to the event. In a list row, trace_id is a data binding
            #   ({"path": "traceId"}): the browser puts in the trace ID of that row.
            "action": {"event": {"name": action}},
        },
        _text(f"{component_id}-label", label),
    ]


def _badge(bottleneck_share: float | None) -> list[dict[str, Any]]:
    """The SeverityBadge component (id "severity") for an incident, or none without a bottleneck.

    A component is a JSON object: its ``id``, the catalog ``component`` it instantiates,
    and that component's properties - here `SeverityBadge.level` and `.contribution`.
    """
    # TODO(step-5): Without a bottleneck there is no badge: return []. Otherwise return one
    #   component: {"id": "severity", "component": "SeverityBadge", "level": <classify_severity>,
    #   "contribution": <the share>}. The cards list it first, and the browser draws it.
    return []


def _card(children: list[str], components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A Card holding `children` (component IDs) in a Column, with all the components."""
    return [
        {"id": "root", "component": "Card", "child": "body"},
        {"id": "body", "component": "Column", "children": children},
        *components,
    ]


def _message_card(title: str, text: str) -> list[dict[str, Any]]:
    """A surface with a title and one line of text (e.g. "nothing found")."""
    return _messages(_card(["title", "detail"], [_text("title", title, "h2"), _text("detail", text)]))


def _tabs(tabs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """A Tabs component ("sections") with one Markdown Text component per (title, Markdown) tab."""
    return [
        {
            "id": "sections",
            "component": "Tabs",
            "tabs": [{"title": title, "child": f"tab-{n}"} for n, (title, _) in enumerate(tabs)],
        },
        *(_report_text(f"tab-{n}", markdown) for n, (_, markdown) in enumerate(tabs)),
    ]


def _link_button(component_id: str, label: str, url: str) -> list[dict[str, Any]]:
    """A button that opens `url` in the browser (the basic catalog's openUrl function)."""
    return [
        {
            "id": component_id,
            "component": "Button",
            "child": f"{component_id}-label",
            "action": {"functionCall": {"call": "openUrl", "args": {"url": url}}},
        },
        _text(f"{component_id}-label", label),
    ]


def _actions(filename: str, report: str, links: dict[str, str] | None) -> list[dict[str, Any]]:
    """The card's action row ("actions"): links to the raw telemetry, and a download."""
    buttons = []
    if links and links.get("trace"):
        buttons += _link_button("open-trace", "Open trace", links["trace"])
    if links and links.get("logs"):
        buttons += _link_button("open-logs", "Open logs", links["logs"])
    children = [c["id"] for c in buttons if c["component"] == "Button"] + ["download"]
    return [{"id": "actions", "component": "Row", "children": children}, *buttons, _download(filename, report)]


def _download(filename: str, content: str) -> dict[str, Any]:
    """A Download component ("download") that saves `content` as `filename`."""
    return {"id": "download", "component": "Download", "label": "Download", "filename": filename, "content": content}


def _incident_row(incident: dict[str, Any]) -> dict[str, str]:
    """One incident as an item of the list's data model."""
    kind = "❌ **failing**" if incident.get("incident") == "error" else "🐢 **slow**"
    trace_id = incident.get("traceId", "")
    details = []
    if incident.get("durationMs"):
        details.append(f"{incident['durationMs']} ms")
    if incident.get("startTime"):
        details.append(incident["startTime"].replace("T", " ")[:19] + " UTC")
    details.append(f"trace {trace_id}")
    return {
        "traceId": trace_id,
        "summary": f"{kind} `{incident.get('name') or '-'}` on `{incident.get('service') or 'unknown service'}`",
        "details": " · ".join(details),
    }


def incident_list_surface(project_id: str, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recent incidents, one row each, with Diagnose and Post-mortem buttons.

    The rows come from the data model (``/incidents``) through a List template: the
    components describe one row, and the renderer repeats it for every item.
    """
    if not incidents:
        return _message_card(
            "✅ No recent incidents", f"No failing or slow requests, or error logs pointing at one, in `{project_id}`."
        )

    failing = sum(1 for i in incidents if i.get("incident") == "error")
    components = [
        _text("title", f"📋 Recent incidents in {project_id}", "h2"),
        _text("detail", f"{failing} failing and {len(incidents) - failing} slow request(s), most important first."),
        {"id": "incidents", "component": "List", "children": {"componentId": "row", "path": "/incidents"}},
        # Text above, buttons below: side by side, a row does not fit a phone screen.
        {"id": "row", "component": "Column", "children": ["row-summary", "row-details", "row-actions"]},
        {"id": "row-actions", "component": "Row", "children": ["diagnose", "post-mortem"]},
        # Paths without a leading slash are relative to the current list item.
        _text("row-summary", {"path": "summary"}),
        _text("row-details", {"path": "details"}, "caption"),
        *_button("diagnose", "Diagnose", "diagnose_incident", {"path": "traceId"}),
        *_button("post-mortem", "Post-mortem", "write_post_mortem", {"path": "traceId"}),
    ]
    rows = [_incident_row(i) for i in incidents]
    return _messages(_card(["title", "detail", "incidents"], components), {"incidents": rows})


# The sections the diagnosis report is assembled from (see `sre_workflow`), in order.
_CASCADE_HEADING = "## ⛓️ Multi-Service Cascade"
_POST_MORTEM_HEADING = "# 🚨 Incident Post-Mortem"


def _split_report(report: str) -> list[tuple[str, str]]:
    """The diagnosis report as (tab title, Markdown) sections."""
    sections: list[tuple[str, str]] = []
    title, rest = "Analysis", report
    for next_title, heading in (("Bottleneck", _CASCADE_HEADING), ("Post-mortem", _POST_MORTEM_HEADING)):
        head, found, tail = rest.partition(heading)
        if found:
            sections.append((title, head))
            title, rest = next_title, found + tail
    sections.append((title, rest))
    return [(t, markdown.strip()) for t, markdown in sections if markdown.strip()]


def diagnosis_surface(
    report: str, trace_id: str | None, bottleneck_share: float | None, links: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """A diagnosis: severity, the report in tabs (its post-mortem is the last tab), console links
    (`gcp_tools.console_links`) and a download."""
    if not trace_id:
        return _message_card("✅ All clear", report)

    badge = _badge(bottleneck_share)
    components = [
        *badge,
        _text("title", "🔬 Incident diagnosis", "h2"),
        _text("trace", f"Trace {trace_id}", "caption"),
        *_tabs(_split_report(report)),
        *_actions(f"diagnosis-{trace_id[:8]}.md", report, links),
    ]
    children = [*(c["id"] for c in badge), "title", "trace", "sections", "actions"]
    return _messages(_card(children, components))


def post_mortem_surface(
    report: str, trace_id: str | None, bottleneck_share: float | None, links: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """A post-mortem: severity, the document (and AI analyst notes in their own tab), console
    links (`gcp_tools.console_links`) and a download."""
    if not trace_id:
        return _message_card("✅ Nothing to write up", report)

    # The card title replaces the document's H1; the AI notes get a tab of their own.
    document, _, notes = report.partition(ANALYSIS_HEADING)
    tabs = [("Post-mortem", document.replace(_POST_MORTEM_HEADING, "", 1).strip())]
    if notes.strip():
        tabs.append(("Analyst notes (AI)", notes.strip()))
    badge = _badge(bottleneck_share)
    components = [
        *badge,
        _text("title", "🚨 Incident post-mortem", "h2"),
        _text("trace", f"Trace {trace_id} · status OPEN until a fix is confirmed", "caption"),
        *_tabs(tabs),
        *_actions(f"post-mortem-{trace_id[:8]}.md", report, links),
    ]
    children = [*(c["id"] for c in badge), "title", "trace", "sections", "actions"]
    return _messages(_card(children, components))
