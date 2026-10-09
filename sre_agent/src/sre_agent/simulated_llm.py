"""A scripted stand-in for Gemini, so the ADK workflow runs without an API key.

ADK lets any `BaseLlm` drive an agent. `SimulatedLlm` reads the request like a model
would - the conversation so far and the tools the agent has - and answers with
fixed rules instead of a neural network:

* The TraceAnalyzer's request lists candidate traces: it answers with one trace ID.
* The LogCorrelator's request has the spans and logs of one trace. If the agent has
  the `query_metrics` tool, it first calls it (real ADK function calls, run by ADK),
  then writes a root-cause analysis from the logs and the tool results. Without the
  tool, its analysis says that it could not check the metrics.

The workflow, the agents, the tool calls and the session state are real ADK; only
the text generation is scripted. Set GEMINI_API_KEY to use Gemini instead.
"""

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

TRACE_ID = re.compile(r"\b[0-9a-f]{32}\b")
# The two time series the mock telemetry has (app/main.py). Real projects have other names:
# there the metric section reports what the queries returned.
METRIC_QUERIES = {
    "CPU utilization (sre-chaos-monkey)": 'metric.type="run.googleapis.com/container/cpu/utilizations"'
    ' AND resource.labels.service_name="sre-chaos-monkey"',
    "Database connections (db-primary)": 'metric.type="cloudsql.googleapis.com/database/postgresql/connection_count"'
    ' AND resource.labels.database_id="db-primary"',
}
NOTE = "_Simulated model: these notes follow fixed rules. Set GEMINI_API_KEY to get Gemini's analysis._"


class SimulatedLlm(BaseLlm):
    """A deterministic model for the SRE agents (see the module docstring)."""

    model: str = "simulated"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts or [] if p.text)
        results = {
            p.function_response.id: p.function_response.response
            for c in llm_request.contents
            for p in c.parts or []
            if p.function_response
        }
        if "Trace Spans:" not in text:
            yield _reply(_pick_trace(text))
        elif "query_metrics" in llm_request.tools_dict and not results:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id=f"metrics-{n}", name="query_metrics", args={"filter_expression": query}
                            )
                        )
                        for n, query in enumerate(METRIC_QUERIES.values())
                    ],
                ),
                usage_metadata=_no_tokens(),
            )
        else:
            metrics = [_metric_line(label, results.get(f"metrics-{n}")) for n, label in enumerate(METRIC_QUERIES)]
            if "query_metrics" not in llm_request.tools_dict:
                metrics = ["- Not checked: this agent has no `query_metrics` tool."]
            yield _reply(_analysis(text, metrics))


def _reply(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), usage_metadata=_no_tokens())


def _no_tokens() -> types.GenerateContentResponseUsageMetadata:
    """The token usage of a scripted answer: none. Without it, ADK warns at each answer."""
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=0, candidates_token_count=0, total_token_count=0
    )


def _pick_trace(text: str) -> str:
    """The trace the user names, if it is a candidate; else the first (best) candidate."""
    question, _, candidates = text.partition("Find the failing trace ID")
    ids = TRACE_ID.findall(candidates)
    named = [t for t in TRACE_ID.findall(question) if t in ids]
    return (named or ids or [""])[0]


def _section(text: str, title: str) -> Any:
    """The JSON value after `title:` in the LogCorrelator's prompt."""
    match = re.search(rf"{title}:\n(.*?)(?:\n\n|\Z)", text, re.S)
    try:
        return json.loads(match.group(1)) if match else None
    except json.JSONDecodeError:
        return None


def _error_message(logs: Any) -> str | None:
    """The message of the most severe log entry (the last one, on a tie)."""
    entries = [e for e in logs if isinstance(e, dict)] if isinstance(logs, list) else []
    for severity in ("CRITICAL", "ERROR"):
        for entry in reversed(entries):
            if entry.get("severity") == severity:
                return (
                    entry.get("message")
                    or entry.get("text_payload")
                    or (entry.get("json_payload") or {}).get("message")
                )
    return None


def _metric_line(label: str, result: Any) -> str:
    """One metric as a line: its latest value, or why there is none."""
    try:
        series = json.loads((result or {}).get("result", ""))
        points = series[0]["points"]
        return f"- {label}: latest value `{points[-1]['value']}` ({len(points)} points)"
    except (ValueError, KeyError, IndexError, TypeError):
        return f"- {label}: no data"


def _analysis(text: str, metrics: list[str]) -> str:
    error = _error_message(_section(text, "Correlated Logs"))
    if error:
        cause = f"The trace failed with `{error}`."
    else:
        cause = "The logs of this trace have no error message, so the cause is not in the telemetry."
    if error and re.search(r"timeout|timed out|connect", error, re.I):
        steps = [
            "Check that the dependency named in the error is up and reachable (firewall, DNS, port).",
            "Check its connection pool: a pool at its limit makes new requests wait until they time out.",
            "Add a timeout and a retry with backoff to the caller, so one slow dependency cannot block it.",
        ]
    else:
        steps = ["Open the trace and its logs in the console, and check the slowest span for a cause."]
    return "\n\n".join(
        [
            NOTE,
            f"## 🔍 Root Cause Analysis\n{cause}",
            "## 📊 Observability Metrics\n" + "\n".join(metrics),
            "## 🛠️ Recommended Mitigation\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)),
        ]
    )
