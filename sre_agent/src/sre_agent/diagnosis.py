"""The SRE diagnosis pipeline, independent of how it is served.

`run_diagnosis` yields progress updates and finally the report. The A2A agent
(`sre_agent.a2a_agent`) turns them into A2A task updates: progress becomes
WORKING status messages and the report becomes the task artifact.
"""

import datetime
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sre_common.middleware import target_project_contextvar

from sre_agent.config import PROJECT_ID
from sre_agent.firestore_strategy import get_sre_session, save_sre_session
from sre_agent.gcp_tools import query_traces
from sre_agent.inventory_client import fetch_topology
from sre_agent.sre_workflow import run_sre_diagnostics

logger = logging.getLogger("sre_agent.diagnosis")


@dataclass(frozen=True)
class Progress:
    """A human-readable step of the diagnosis, shown live in the chat."""

    text: str


@dataclass(frozen=True)
class Report:
    """The final Markdown diagnosis report."""

    text: str


class DiagnosisError(RuntimeError):
    """Raised when the diagnosis cannot run at all (e.g. the Trace API is unreachable)."""


async def run_diagnosis(
    prompt: str,
    project_id: str | None = None,
    refresh: bool = False,
    conversation_id: str | None = None,
) -> AsyncIterator[Progress | Report]:
    """Diagnoses the target project and yields progress, then exactly one Report.

    Args:
        prompt: The user's request, recorded in the session history.
        project_id: The GCP project to diagnose. Defaults to the service's project.
        refresh: Force the Inventory Agent to rescan the project's topology.
        conversation_id: When set, the run is appended to that session's history.

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
        traces_json = await query_traces(project_id=resolved_project, limit=10)
    except Exception as e:
        raise DiagnosisError(f"Trace API query failed: {e!s}") from e

    # 3. The ADK multi-agent workflow (or its deterministic tier without a key)
    yield Progress("🧠 Running multi-agent ADK correlation workflow (TraceAnalyzer + LogCorrelator)...")
    report = await run_sre_diagnostics(traces_json=traces_json, project_id=resolved_project)
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
