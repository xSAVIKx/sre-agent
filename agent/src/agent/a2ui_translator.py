"""A2UI Translator for SRE Agent.

This module parses Markdown diagnostic outputs from the SRE agent and converts
them into structured A2UI (Agent-to-User Interface) declarative JSON payloads,
enabling rich rendering on custom frontends.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger("sre_agent.a2ui_translator")


# Bottleneck share of the whole trace at or above which an incident gets each level.
SEVERITY_THRESHOLDS: tuple[tuple[float, str], ...] = ((90.0, "SEV1"), (50.0, "SEV2"), (0.0, "SEV3"))


def classify_severity(text: str) -> dict[str, Any] | None:
    """Builds a severity badge from the bottleneck contribution in a cascade report.

    `analyze_trace_cascade` reports the bottleneck as e.g.
    ``*   **Self-Execution Time**: `10200 ms` (99.3% of total trace)``. The larger the
    share of the request one span is responsible for, the more clearly a single
    component is down, and the more severe the incident.

    Args:
        text: A diagnosis or post-mortem report in Markdown.

    Returns:
        A ``severity_badge`` A2UI component - ``{"type": "severity_badge", "level":
        "SEV1", "contribution": 99.3}`` - or None when the report has no bottleneck.
    """
    match = re.search(r"\(([\d.]+)% of total trace\)", text)
    if not match:
        return None
    contribution = float(match.group(1))
    level = next(name for threshold, name in SEVERITY_THRESHOLDS if contribution >= threshold)
    return {"type": "severity_badge", "level": level, "contribution": contribution}


def translate_markdown_to_a2ui(text: str) -> dict[str, Any]:
    """Translates Markdown responses into structured A2UI declarative JSON.

    If the text is already JSON matching the A2UI schema, it is returned as-is.
    If it's an SRE diagnostics report, it maps fields (trace ID, logs, mitigation steps)
    into custom UI components. Otherwise, it wraps the text in a simple container.

    Args:
        text: Raw agent text output.

    Returns:
        A dictionary representation of the A2UI payload.
    """
    if not text:
        return {"type": "container", "components": [{"type": "text", "content": ""}]}

    # 1. Check if the output is already JSON
    trimmed = text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            data = json.loads(trimmed)
            if isinstance(data, dict) and "type" in data:
                return data
        except Exception:
            pass

    # 2. Check if this is a post-mortem report
    if "# 🚨 Incident Post-Mortem" in text or "Incident Post-Mortem" in text:
        title_match = re.search(r"#\s*🚨\s*(Incident Post-Mortem[^\n]*)", text)
        title = title_match.group(1).strip() if title_match else "Incident Post-Mortem Report"
        badge = classify_severity(text)
        return {
            "type": "container",
            "components": [
                *([badge] if badge else []),
                {
                    "type": "alert",
                    "level": "success",
                    "title": title,
                    "text": "The SRE diagnostics agent has auto-generated the incident post-mortem report. You can download the complete markdown document using the button below.",
                },
                {"type": "section", "title": "Document Preview", "content": text},
                {
                    "type": "download_button",
                    "text": "Download Post-Mortem Markdown",
                    "filename": "post_mortem.md",
                    "content": text,
                },
            ],
        }

    # 3. Parse SRE Incident Diagnosis Report
    trace_match = re.search(r"\*\*Anomalous Trace ID\*\*:\s*`([^`]+)`", text)
    service_match = re.search(r"\*\*Root Service\*\*:\s*`([^`]+)`", text)

    trace_id = trace_match.group(1).strip() if trace_match else None
    root_service = service_match.group(1).strip() if service_match else None

    # Extract logs code block (if any)
    log_match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    logs = log_match.group(1).strip() if log_match else None

    # Extract Root Cause Analysis (between Root Cause Analysis header and next header or code block)
    rc_match = re.search(r"## 🔍 Root Cause Analysis\n(.*?)(?=##|```|$)", text, re.DOTALL)
    root_cause = rc_match.group(1).strip() if rc_match else None

    # Extract Recommended Mitigation list items
    mitigations: list[str] = []
    mitigation_section = re.search(r"## 🛠️ Recommended Mitigation\n(.*)", text, re.DOTALL)
    if mitigation_section:
        mitigation_text = mitigation_section.group(1)
        # Parse numbered list entries (e.g. 1. **Check Database Health**: Description)
        items = re.findall(r"\d+\.\s*\*\*(.*?)\*\*:\s*(.*)", mitigation_text)
        if items:
            mitigations = [f"**{title.strip()}**: {desc.strip()}" for title, desc in items]
        else:
            # Fallback simple line items parsing
            lines = re.findall(r"[-*+\d.]+\s*(.*)", mitigation_text)
            mitigations = [line.strip() for line in lines if line.strip()]

    # 3. Assemble SRE A2UI Dashboard Container
    if trace_id or root_service:
        components: list[dict[str, Any]] = [
            {
                "type": "alert",
                "level": "error",
                "title": "Incident Detected",
                "text": f"Service failure detected on root service `{root_service or 'unknown'}`. Anomalous trace `{trace_id or 'unknown'}` indicates a backend exception or timeout.",
            },
            {
                "type": "card",
                "title": "Incident Metadata",
                "fields": [
                    {"label": "Anomalous Trace ID", "value": trace_id or "Unknown"},
                    {"label": "Root Service", "value": root_service or "Unknown"},
                ],
            },
        ]

        if root_cause:
            components.append({"type": "section", "title": "🔍 Root Cause Analysis", "content": root_cause})

        if logs:
            components.append({"type": "code_block", "title": "Correlated Diagnostics Logs", "code": logs})

        if mitigations:
            components.append({"type": "list", "title": "🛠️ Recommended Mitigations", "items": mitigations})

        return {"type": "container", "components": components}

    # 4. Success / Health Check Alert Fallback
    if any(
        x in text.lower()
        for x in ("no anomalous traces", "diagnostics completed", "successfully connected", "redeploy completed")
    ):
        title = "Diagnostics Clean"
        if "successfully connected" in text.lower():
            title = "Agent Health Clean"
        return {
            "type": "container",
            "components": [{"type": "alert", "level": "success", "title": title, "text": text}],
        }

    # 5. Standard Text Chat Fallback
    return {"type": "container", "components": [{"type": "text", "content": text}]}
