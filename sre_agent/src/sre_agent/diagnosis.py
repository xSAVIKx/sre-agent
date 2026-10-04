"""The SRE agent's skills, independent of how they are served.

One async generator per A2A skill. Each yields progress updates and finally
exactly one Report; the A2A agent (`sre_agent.a2a_agent`) turns them into A2A
task updates: progress becomes WORKING status messages, the report the artifact.

* `run_list_incidents` - the recent failing or slow requests. Seconds, no model.
* `run_diagnosis` - root cause of one incident: the ADK TraceAnalyzer + LogCorrelator.
* `run_post_mortem` - the post-mortem of one incident, plus a model-written
  analysis when a Gemini key is configured.
"""

import datetime
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sre_common.middleware import target_project_contextvar

from sre_agent.config import PROJECT_ID
from sre_agent.firestore_strategy import get_sre_session, save_sre_session
from sre_agent.gcp_tools import TRACE_SCAN_SIZE, generate_post_mortem, query_traces
from sre_agent.incidents import find_incident
from sre_agent.inventory_client import fetch_topology
from sre_agent.post_mortem_analysis import analysis_enabled, analyze_post_mortem
from sre_agent.sre_workflow import run_sre_diagnostics

logger = logging.getLogger("sre_agent.diagnosis")


@dataclass(frozen=True)
class Progress:
    """A human-readable step of the diagnosis, shown live in the chat."""

    text: str


@dataclass(frozen=True)
class Report:
    """A skill's result: Markdown for people, and optionally structured data for programs."""

    text: str
    data: dict[str, Any] | None = None


class DiagnosisError(RuntimeError):
    """Raised when the diagnosis cannot run at all (e.g. the Trace API is unreachable)."""


async def run_diagnosis(
    prompt: str,
    project_id: str | None = None,
    refresh: bool = False,
    conversation_id: str | None = None,
    trace_id: str | None = None,
) -> AsyncIterator[Progress | Report]:
    """Diagnoses the target project and yields progress, then exactly one Report.

    Args:
        prompt: The user's request. The TraceAnalyzer reads it to pick the incident,
            and it is recorded in the session history.
        project_id: The GCP project to diagnose. Defaults to the service's project.
        refresh: Force the Inventory Agent to rescan the project's topology.
        conversation_id: When set, the run is appended to that session's history.
        trace_id: Diagnose this trace instead of picking the incident.

    Raises:
        DiagnosisError: If recent traces cannot be retrieved.
    """
    resolved_project = project_id or PROJECT_ID
    target_project_contextvar.set(resolved_project)

    # 1. Project topology from the Inventory Agent
    yield Progress(f"🔧 Contacting Inventory Agent to fetch topology for project `{resolved_project}`...")
    topology: dict[str, Any] = {}
    try:
        topology = await fetch_topology(resolved_project, refresh=refresh)
        if topology.get("status") == "DISCOVERING":
            yield Progress(
                "⚠️ Target project infrastructure discovery in progress. "
                "Diagnostic run may use cached or incomplete topology data."
            )
        else:
            services = topology.get("discovered_resources", {}).get("services", [])
            yield Progress(f"✅ Topology cached successfully. Resolved {len(services)} active compute services.")
    except Exception as e:
        logger.error(f"Failed to query the Inventory Agent: {e}")
        yield Progress("⚠️ Inventory Agent query failed. Proceeding with default service topology parameters.")

    # 2. Recent traces
    yield Progress(f"🔍 Fetching recent traces from project `{resolved_project}`...")
    try:
        # The whole scan: the newest few are often the agents' own (ignored) requests.
        traces_json = await query_traces(project_id=resolved_project, limit=TRACE_SCAN_SIZE)
    except Exception as e:
        raise DiagnosisError(f"Trace API query failed: {e!s}") from e

    # 3. The ADK multi-agent workflow (or its deterministic tier without a key)
    yield Progress("🧠 Running multi-agent ADK correlation workflow (TraceAnalyzer + LogCorrelator)...")
    report = await run_sre_diagnostics(
        traces_json=traces_json, project_id=resolved_project, question=prompt, trace_id=trace_id
    )
    yield Progress("✅ Diagnostics complete. Generating Markdown report...")

    # 4. Private session history
    if conversation_id:
        sess = await get_sre_session(conversation_id) or {}
        history = sess.get("history", [])
        history.append(
            {
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "prompt": prompt,
                "traces_analyzed": traces_json,
                "topology": topology,
                "report": report,
            }
        )
        await save_sre_session(conversation_id, history)

    yield Report(report)


def _incident_row(n: int, incident: dict[str, Any]) -> str:
    kind = "❌ failing" if incident.get("incident") == "error" else "🐢 slow"
    duration = f"{incident['durationMs']} ms" if incident.get("durationMs") else "-"
    started = (incident.get("startTime") or "-").replace("T", " ")[:19]
    request = (incident.get("name") or "-").replace("|", "\\|")[:60]
    return (
        f"| {n} | {kind} | `{incident.get('service') or '-'}` | {request} | {duration} | {started} "
        f"| `{incident.get('traceId', '')}` |"
    )


async def run_list_incidents(project_id: str | None = None, limit: int = 10) -> AsyncIterator[Progress | Report]:
    """Lists the recent requests worth diagnosing, best candidate first. No model calls.

    Args:
        project_id: The GCP project to scan. Defaults to the service's project.
        limit: The most incidents to list.

    Raises:
        DiagnosisError: If recent traces cannot be retrieved.
    """
    resolved_project = project_id or PROJECT_ID
    target_project_contextvar.set(resolved_project)

    yield Progress(f"🔍 Scanning the most recent traces in project `{resolved_project}`...")
    try:
        traces_json = await query_traces(project_id=resolved_project, limit=TRACE_SCAN_SIZE)
    except Exception as e:
        raise DiagnosisError(f"Trace API query failed: {e!s}") from e

    # Ranked failing/slow traces, or the request the newest error log points at.
    _, incidents = await find_incident(traces_json, resolved_project)
    incidents = incidents[:limit]

    if not incidents:
        text = (
            f"## ✅ No recent incidents\n\nNo failing or slow requests in the last {TRACE_SCAN_SIZE} traces of "
            f"`{resolved_project}`, and no error logs that point at a request."
        )
    else:
        failing = sum(1 for i in incidents if i.get("incident") == "error")
        text = (
            f"## 📋 Recent incidents in `{resolved_project}`\n\n"
            f"{failing} failing and {len(incidents) - failing} slow request(s), most important first. "
            "Ask to diagnose one, or for its post-mortem, by its trace ID.\n\n"
            "| # | Kind | Service | Request | Duration | Started (UTC) | Trace ID |\n"
            "|---|---|---|---|---|---|---|\n" + "\n".join(_incident_row(n, i) for n, i in enumerate(incidents, 1))
        )
    data = {"kind": "incident_list", "project_id": resolved_project, "incidents": incidents}
    yield Report(text, data)


async def run_post_mortem(
    prompt: str = "", project_id: str | None = None, trace_id: str | None = None
) -> AsyncIterator[Progress | Report]:
    """Writes the post-mortem of one incident: the trace's evidence, rendered from a template.

    With a Gemini key configured, a model-written analysis section is appended,
    clearly marked as such; without one (or if the model fails) the template stands alone.

    Args:
        prompt: The user's request, given to the model as context.
        project_id: The GCP project. Defaults to the service's project.
        trace_id: The incident's trace. Defaults to the most important recent incident.
    """
    resolved_project = project_id or PROJECT_ID
    target_project_contextvar.set(resolved_project)

    if not trace_id:
        yield Progress(f"🔍 Picking the most important recent incident in `{resolved_project}`...")
        traces_json = await query_traces(project_id=resolved_project, limit=TRACE_SCAN_SIZE)
        incident, _ = await find_incident(traces_json, resolved_project)
        if incident is None:
            yield Report(
                "## ✅ Nothing to write up\n\nNo recent failing or slow requests, and no error logs that point at one.",
                {"kind": "post_mortem", "project_id": resolved_project, "trace_id": None},
            )
            return
        trace_id = incident["traceId"]

    yield Progress(f"📝 Writing the post-mortem for trace `{trace_id}` from its spans and logs...")
    report = await generate_post_mortem(trace_id, resolved_project)

    analysis = ""
    if not report.startswith("Error:") and analysis_enabled():
        yield Progress("🧠 Asking Gemini for an analysis of the evidence...")
        analysis = await analyze_post_mortem(report, prompt)
    if analysis:
        report = f"{report}\n\n{analysis}"
    yield Report(
        report,
        {"kind": "post_mortem", "project_id": resolved_project, "trace_id": trace_id, "llm_analysis": bool(analysis)},
    )
