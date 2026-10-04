# GENERATED from sre_agent/src/sre_agent/incidents.py by scripts/sync_skill.py - do not edit.
# Change the engine module instead, then run: uv run python scripts/sync_skill.py
"""Which recent request is the incident worth diagnosing.

One shared rule for every diagnosis path (offline tier, ADK tier, pre-check):

1. Ignore the agents' own traffic and probes. All services report to the same
   Cloud Trace project, so a long chat with the agent is otherwise the slowest
   "incident" there is (SRE_IGNORED_SERVICES, comma-separated).
2. Failing requests outrank slow ones; among failures the newest wins, among
   slow requests (over SRE_SLOW_TRACE_MS) the slowest.
3. No bad trace, but error logs? The newest error log that carries a trace ID
   points at the request to diagnose.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger("sre_agent.incidents")

IGNORED_SERVICES = frozenset(
    s.strip()
    for s in os.getenv("SRE_IGNORED_SERVICES", "sre-agent,sre-sub-agent,inventory-agent").split(",")
    if s.strip()
)
SLOW_TRACE_MS = int(os.getenv("SRE_SLOW_TRACE_MS", "5000"))
_PROBE_MARKERS = ("diagnose", "health", "warmup")


def _is_ignored(trace: dict[str, Any]) -> bool:
    name = (trace.get("name") or "").lower()
    return trace.get("service") in IGNORED_SERVICES or name == "/" or any(m in name for m in _PROBE_MARKERS)


def classify(trace: dict[str, Any]) -> str | None:
    """'error', 'slow', or None for a healthy request."""
    if trace.get("error") is True:
        return "error"
    if (trace.get("durationMs") or 0) > SLOW_TRACE_MS:
        return "slow"
    return None


def rank_incidents(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recent requests worth diagnosing, best candidate first, each tagged with
    its ``incident`` kind ('error' or 'slow')."""
    candidates = [dict(t, incident=classify(t)) for t in traces if not _is_ignored(t) and classify(t)]
    errors = sorted(
        (t for t in candidates if t["incident"] == "error"), key=lambda t: t.get("startTime", ""), reverse=True
    )
    slow = sorted(
        (t for t in candidates if t["incident"] == "slow"), key=lambda t: t.get("durationMs", 0), reverse=True
    )
    return errors + slow


def parse_traces(traces_json: str) -> list[dict[str, Any]]:
    """The trace summaries from `query_traces` output ([] if it reported an error)."""
    try:
        data = json.loads(traces_json)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


async def find_incident(traces_json: str, project_id: str | None = None) -> tuple[dict[str, Any] | None, list]:
    """Picks the request to diagnose.

    Returns:
        (incident, candidates): the chosen trace summary or None if everything looks
        healthy, and the ranked candidates (what the ADK TraceAnalyzer chooses from).
    """
    candidates = rank_incidents(parse_traces(traces_json))
    if candidates:
        return candidates[0], candidates

    incident = await _incident_from_error_logs(project_id)
    return incident, [incident] if incident else []


async def _incident_from_error_logs(project_id: str | None) -> dict[str, Any] | None:
    """The newest error log (outside the agents' own services) that names its trace."""
    from .gcp_tools import IS_MOCK, query_logs

    query = "severity>=ERROR"
    if not IS_MOCK and IGNORED_SERVICES:  # mock logs only ever come from the target app
        query += "".join(f' AND NOT resource.labels.service_name="{s}"' for s in sorted(IGNORED_SERVICES))
    try:
        logs = json.loads(await query_logs(query=query, project_id=project_id, limit=20))
    except Exception as e:
        logger.warning(f"Could not read recent error logs: {e}")
        return None
    if not isinstance(logs, list):
        return None
    for log in logs:
        if log.get("severity") not in ("ERROR", "CRITICAL"):
            continue
        trace_id = (log.get("trace") or log.get("traceId") or "").rsplit("/", 1)[-1]
        if trace_id:
            message = log.get("text_payload") or (log.get("json_payload") or {}).get("message") or "error log"
            return {"traceId": trace_id, "name": message[:120], "error": True, "incident": "error", "source": "logs"}
    return None
