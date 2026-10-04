"""Antigravity SRE Agent Orchestrator runtime configuration.

This module defines the Orchestrator agent's configuration using the Google
Antigravity SDK. It configures system instructions to delegate SRE queries
to the SRE Sub-Agent, registers the A2A tool, and establishes safety policies.
"""

import asyncio
import contextvars
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sre_common.a2a_client import call_agent

# Fail-safe OpenTelemetry imports for tracer initialization. These names are a
# capability probe for HAS_OTEL, not call sites - hence the noqa.
try:
    from opentelemetry import trace  # noqa: F401
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # noqa: F401
    from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: F401

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

# Setup logging
logger = logging.getLogger("orchestrator_agent")

# Resilient imports for google-antigravity
try:
    from google.antigravity import Agent, LocalAgentConfig
    from google.antigravity.hooks.hooks import HookContext, OnToolErrorHook
    from google.antigravity.hooks.policy import allow, ask_user, deny
    from google.antigravity.types import Text, Thought, ToolCall, ToolResult

    # An empty value (e.g. docker-compose's `${GEMINI_API_KEY:-}`) counts as no key.
    HAS_ANTIGRAVITY = bool(os.environ.get("GEMINI_API_KEY"))
except ImportError:
    HAS_ANTIGRAVITY = False


@dataclass(frozen=True)
class MockPolicy:
    """Simulation-mode stand-in for an Antigravity policy rule."""

    tool: str
    decision: str  # "allow" | "deny" | "ask_user"


def evaluate_mock_policy(policies: list[Any], tool_name: str) -> str:
    """Decides a tool call the way the Antigravity harness does, for simulation mode.

    Mirrors the SDK precedence: specific deny > specific ask > specific allow >
    wildcard deny > wildcard ask > wildcard allow. With no matching rule the call
    is denied, so a missing policy fails closed.

    Args:
        policies: The `MockPolicy` rules from the agent config.
        tool_name: The name of the tool the model wants to call.

    Returns:
        "allow", "deny" or "ask_user".
    """
    rules = [p for p in policies if isinstance(p, MockPolicy)]
    for target in (tool_name, "*"):
        for decision in ("deny", "ask_user", "allow"):
            if any(p.tool == target and p.decision == decision for p in rules):
                return decision
    return "deny"


_TRACE_ID = re.compile(r"\b[0-9a-f]{32}\b")
_NUMBERED = re.compile(r"\d+\.\s")


def mock_route(prompt: str) -> tuple[str, dict[str, Any]] | None:
    """Simulation mode's stand-in for the model choosing a tool: (tool name, arguments) or None.

    Keyword rules in the order a person would mean them: an explicit post-mortem, an
    explicit diagnosis, a question about what is failing, then anything diagnostic.
    """
    text = prompt.lower()
    trace = _TRACE_ID.search(text)
    trace_args = {"trace_id": trace.group(0)} if trace else {}
    if any(x in text for x in ("post-mortem", "postmortem", "post mortem")):
        return "write_post_mortem", {"prompt": prompt, **trace_args}
    if trace or any(x in text for x in ("diagnose", "root cause", "why")):
        return "diagnose_sre", {"prompt": prompt, **trace_args}
    if any(x in text for x in ("list", "latest", "recent", "failing", "broken")):
        return "list_incidents", {}
    if any(x in text for x in ("error", "trace", "latency", "sre", "slow", "fail")):
        return "diagnose_sre", {"prompt": prompt}
    return None


def summarize_report(report: str) -> str:
    """Simulation mode's stand-in for the model's short reply: the report's opening lines.

    The real model writes the summary itself (see SYSTEM_INSTRUCTIONS); either way the
    UI shows the full report as a card under the reply.
    """
    if report.startswith("Error:") or "blocked by the safety policy" in report:
        return report
    # The first line of plain prose: not a heading, list item, table row or code fence.
    prose = (line.strip() for line in report.splitlines())
    first = next((line for line in prose if line and line[0] not in "#*-|`>" and not _NUMBERED.match(line)), "")
    return f"{first} The full result is below." if first else report


# Global database to persist mock session history in local simulation mode
MOCK_HISTORY_DB: dict[str, list[dict[str, Any]]] = {}

if not HAS_ANTIGRAVITY:
    logger.warning(
        "google-antigravity is not active or GEMINI_API_KEY is missing. Using simulated agent config fallbacks."
    )

    class Text:
        def __init__(self, text: str, step_index: int = 0) -> None:
            self.text = text
            self.step_index = step_index

    class Thought:
        def __init__(self, text: str, step_index: int = 0) -> None:
            self.text = text
            self.step_index = step_index

    class ToolCall:
        def __init__(self, name: str, args: dict[str, Any], id: str = "mock_tool_call_id") -> None:
            self.name = name
            self.args = args
            self.id = id

    class ToolResult:
        def __init__(self, name: str, result: str, id: str = "mock_tool_call_id") -> None:
            self.name = name
            self.result = result
            self.id = id

    class MockStep:
        def __init__(self, **kwargs) -> None:
            self.id = ""
            self.step_index = 0
            self.type = "TEXT_RESPONSE"
            self.source = "USER"
            self.target = "TARGET_UNSPECIFIED"
            self.status = "DONE"
            self.content = ""
            self.content_delta = None
            self.thinking = None
            self.thinking_delta = None
            self.tool_calls = []
            self.error = None
            self.is_complete_response = True
            self.structured_output = None
            self.usage_metadata = None

            for k, v in kwargs.items():
                if k == "type" and v == "TEXT":
                    v = "TEXT_RESPONSE"
                elif k == "status" and v == "SUCCESS":
                    v = "DONE"
                elif k == "target" and v == "MODEL":
                    v = "TARGET_UNSPECIFIED"
                elif k == "target" and v == "USER":
                    v = "TARGET_USER"
                setattr(self, k, v)

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {
                "id": self.id,
                "step_index": self.step_index,
                "type": self.type,
                "source": self.source,
                "target": self.target,
                "status": self.status,
                "content": self.content,
                "thinking": self.thinking,
                "tool_calls": self.tool_calls,
                "error": self.error,
                "is_complete_response": self.is_complete_response,
            }

    class MockResponse:
        def __init__(
            self, route: tuple[str, dict[str, Any]] | None, prompt: str, conversation: Any, policies: list[Any]
        ) -> None:
            self._route = route
            self.prompt = prompt
            self.conversation = conversation
            self.policies = policies
            self._text = ""

        @property
        def chunks(self) -> Any:
            async def _gen():
                if self._route:
                    # Stand-in for the model picking a tool. The call still goes through
                    # the configured policies, exactly as it would in the real harness.
                    tool_name, args = self._route
                    yield ToolCall(name=tool_name, args=args)
                    decision = evaluate_mock_policy(self.policies, tool_name)
                    if decision == "allow":
                        try:
                            # Looked up at call time, so tests can patch the tool.
                            result = await globals()[tool_name](**args)
                            diagnosis = summarize_report(result)
                        except Exception as diag_err:
                            logger.error(f"Mock orchestrator failed to run {tool_name}: {diag_err}")
                            diagnosis = f"Error: {tool_name} failed: {diag_err!s}"
                    else:
                        logger.warning(f"Policy decision for {tool_name} is '{decision}'. Tool call blocked.")
                        diagnosis = (
                            f"The `{tool_name}` tool call was blocked by the safety policy (decision: {decision})."
                        )
                    self._text = diagnosis

                    words = diagnosis.split(" ")
                    for i, word in enumerate(words):
                        yield Text(text=word + (" " if i < len(words) - 1 else ""))
                        await asyncio.sleep(0.005)

                    model_step = MockStep(
                        step_index=len(self.conversation._steps),
                        type="TEXT_RESPONSE",
                        source="MODEL",
                        target="TARGET_USER",
                        status="DONE",
                        content=self._text,
                        thinking=f"Delegated to the {tool_name} tool.",
                        tool_calls=[{"name": tool_name, "args": args}],
                    )
                    self.conversation._steps.append(model_step)
                else:
                    yield Thought(text="Simulating basic greeting response...")
                    await asyncio.sleep(0.5)
                    response_text = f"Simulation mode: analyzed prompt '{self.prompt}'."
                    self._text = response_text
                    words = response_text.split(" ")
                    for i, word in enumerate(words):
                        yield Text(text=word + (" " if i < len(words) - 1 else ""))
                        await asyncio.sleep(0.02)

                    model_step = MockStep(
                        step_index=len(self.conversation._steps),
                        type="TEXT_RESPONSE",
                        source="MODEL",
                        target="TARGET_USER",
                        status="DONE",
                        content=self._text,
                        thinking="Simulating basic greeting response...",
                    )
                    self.conversation._steps.append(model_step)

            return _gen()

    class MockConversation:
        def __init__(self) -> None:
            self._steps = []

        @property
        def history(self) -> list[Any]:
            return self._steps

    class MockAgent:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.conversation = MockConversation()

        async def __aenter__(self) -> "MockAgent":
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        async def chat(self, prompt: str) -> Any:
            route = mock_route(prompt)

            # Setup session in database
            conv_id = self.conversation_id
            if conv_id not in MOCK_HISTORY_DB:
                MOCK_HISTORY_DB[conv_id] = []

            # Record user step
            user_step = MockStep(
                step_index=len(self.conversation._steps),
                type="TEXT",
                source="USER",
                target="MODEL",
                status="SUCCESS",
                content=prompt,
            )
            self.conversation._steps.append(user_step)

            class MockResponseWrapper:
                def __init__(self, route: Any, prompt: str, conversation: Any, policies: list[Any]) -> None:
                    self.response = MockResponse(route, prompt, conversation, policies)

                @property
                def chunks(self):
                    return self.response.chunks

                async def text(self):
                    # Consume the chunks to build the full text
                    async for _ in self.response.chunks:
                        pass
                    return self.response._text

                async def cancel(self):
                    pass

            return MockResponseWrapper(route, prompt, self.conversation, self.config.policies)

        @property
        def conversation_id(self) -> str | None:
            if not getattr(self.config, "conversation_id", None):
                import uuid

                self.config.conversation_id = f"mock-{uuid.uuid4().hex}"
            return self.config.conversation_id

    Agent = MockAgent

    class LocalAgentConfig:
        def __init__(
            self,
            system_instructions: str,
            tools: list[Any],
            policies: list[Any] | None = None,
            hooks: list[Any] | None = None,
        ) -> None:
            self.system_instructions = system_instructions
            self.tools = tools
            self.policies = policies or []
            self.hooks = hooks or []

    def deny(target: str) -> Any:
        return MockPolicy(tool=target, decision="deny")

    def allow(target: str) -> Any:
        return MockPolicy(tool=target, decision="allow")

    def ask_user(target: str, *, handler: Any = None) -> Any:
        return MockPolicy(tool=target, decision="ask_user")

    class OnToolErrorHook:
        pass

    class HookContext:
        pass


class ToolRegistry:
    """Registry to manage and retrieve custom agent tools."""

    def __init__(self) -> None:
        self._tools = []

    def register(self, func: Any) -> Any:
        if func not in self._tools:
            self._tools.append(func)
        return func

    def get_tools(self) -> list[Any]:
        return self._tools


registry = ToolRegistry()


def register_tool(func: Any) -> Any:
    return registry.register(func)


class SreToolErrorHook(OnToolErrorHook):
    """Custom hook to handle and recover from SRE tool execution errors."""

    async def run(self, context: HookContext, data: Exception) -> str | None:
        logger.error(f"Orchestrator Tool Error: {data}")
        return f"[System: Failed to call SRE Diagnostics Sub-Agent: {data}]"


@dataclass
class DiagnosisSink:
    """Per-request channel between the `diagnose_sre` tool and the chat stream.

    The route installs one in `diagnosis_sink` before the agent starts; the tool
    pushes the SRE sub-agent's progress messages into it while it waits, and
    leaves the full report behind so the UI can render the post-mortem even if
    the model only summarizes it.
    """

    on_progress: Callable[[str], None] = lambda _text: None
    report: str = ""
    # The SRE skill that produced `report`, e.g. "list_incidents".
    skill: str = ""
    # The A2A contextId for this chat, so the SRE agent keeps one session per conversation.
    context_id: str = ""
    progress: list[str] = field(default_factory=list)

    def emit(self, text: str) -> None:
        self.progress.append(text)
        self.on_progress(text)


diagnosis_sink: contextvars.ContextVar[DiagnosisSink | None] = contextvars.ContextVar("diagnosis_sink", default=None)


def _emit_progress(text: str) -> None:
    sink = diagnosis_sink.get()
    if sink is not None:
        sink.emit(text)


SIMULATION_PROJECT = "simulation-project-123"


async def _run_in_process(skill: str, prompt: str, project_id: str | None, trace_id: str | None) -> str:
    """Runs an SRE skill in this process (standalone simulation: no SRE service is running)."""
    resolved_project = project_id or os.environ.get("GCP_PROJECT") or SIMULATION_PROJECT
    if skill == "diagnose_incident":
        from sre_agent.gcp_tools import TRACE_SCAN_SIZE, query_traces
        from sre_agent.sre_workflow import run_sre_diagnostics

        traces_json = await query_traces(project_id=resolved_project, limit=TRACE_SCAN_SIZE)
        return await run_sre_diagnostics(traces_json, resolved_project, question=prompt, trace_id=trace_id)

    from sre_agent.diagnosis import Progress, run_list_incidents, run_post_mortem

    run = (
        run_list_incidents(project_id=resolved_project)
        if skill == "list_incidents"
        else run_post_mortem(prompt=prompt, project_id=resolved_project, trace_id=trace_id)
    )
    report = ""
    async for update in run:
        if isinstance(update, Progress):
            _emit_progress(update.text)
        else:
            report = update.text
    return report


async def _call_sre_skill(
    skill: str, prompt: str, project_id: str | None = None, trace_id: str | None = None, refresh: bool = False
) -> str:
    """Runs one of the SRE agent's A2A skills and returns its Markdown result.

    The SRE engine's agent card (/.well-known/agent-card.json) lists the skills; the
    request metadata names the one to run. Progress arrives as task status updates
    (forwarded to the chat), the result as the task artifact. The result is also left
    in the request's `DiagnosisSink`, so the UI can show it in full as a card.
    """
    sre_agent_url = os.getenv("SRE_AGENT_URL")
    mock_mode = os.getenv("MOCK_GCP", "false").lower() == "true"

    # Standalone simulation (simulate_incident.py): no sub-agent service is
    # running, so run the same pipelines in-process. When SRE_AGENT_URL is set
    # (docker-compose, Cloud Run) always delegate over A2A.
    if mock_mode and not sre_agent_url:
        logger.info(f"MOCK_GCP is true and SRE_AGENT_URL is unset. Running {skill} in-process.")
        _emit_progress(f"Running the SRE skill `{skill}` in-process (simulation mode)...")
        try:
            report = await _run_in_process(skill, prompt, project_id, trace_id)
        except Exception as mock_err:
            logger.error(f"Failed to run {skill} in-process: {mock_err}")
            report = f"Error: in-process SRE skill {skill} failed: {mock_err!s}"
    else:
        base_url = sre_agent_url or "http://sre-agent:8080"
        sink = diagnosis_sink.get()
        logger.info(f"Calling the SRE agent's {skill} skill over A2A: {base_url}")
        _emit_progress(f"Contacting the SRE diagnostics sub-agent over A2A (skill `{skill}`)...")
        metadata: dict[str, Any] = {
            "skill": skill,
            "project_id": project_id or os.environ.get("GCP_PROJECT", ""),
            "refresh": refresh,
        }
        if trace_id:
            metadata["trace_id"] = trace_id
        try:
            result = await call_agent(
                base_url,
                prompt,
                metadata,
                context_id=sink.context_id if sink else "",
                on_progress=_emit_progress,
            )
            report = result.text
        except Exception as e:
            logger.error(f"Failed to communicate with SRE sub-agent: {e}")
            report = f"Error: Failed to contact SRE Sub-Agent: {e!s}"

    sink = diagnosis_sink.get()
    if sink is not None:
        sink.report, sink.skill = report, skill
    return report


@register_tool
async def list_incidents(project_id: str | None = None) -> str:
    """Lists the recent failing and slow requests in the project, most important first.

    Fast (no deep analysis). Use it for questions like "what is failing?", "any recent
    errors?" or "what are the latest incidents?".

    Args:
        project_id: The GCP Project ID. If None, uses the default project.

    Returns:
        A Markdown table of incidents with their trace IDs.
    """
    return await _call_sre_skill("list_incidents", "List the recent incidents.", project_id)


@register_tool
async def diagnose_sre(
    prompt: str, project_id: str | None = None, refresh: bool = False, trace_id: str | None = None
) -> str:
    """Delegates root-cause diagnosis of an incident to the SRE Sub-Agent.

    It picks the request the prompt is about (or the most important recent one),
    finds the bottleneck span, correlates logs and metrics, and appends a post-mortem.
    Takes 20-30 seconds.

    Args:
        prompt: The user's question, explaining the issue or symptoms.
        project_id: The GCP Project ID. If None, uses default project.
        refresh: Set True to force a fresh infrastructure rescan/discovery.
        trace_id: The 32-character trace ID to diagnose, when the user names one.

    Returns:
        A markdown-formatted SRE incident diagnosis report.
    """
    return await _call_sre_skill("diagnose_incident", prompt, project_id, trace_id, refresh)


@register_tool
async def write_post_mortem(prompt: str, trace_id: str | None = None, project_id: str | None = None) -> str:
    """Writes the incident post-mortem for one trace: overview, timeline, root cause, next steps.

    Args:
        prompt: The user's request, for context.
        trace_id: The 32-character trace ID of the incident. If None, the most important
            recent incident is written up.
        project_id: The GCP Project ID. If None, uses the default project.

    Returns:
        A Markdown post-mortem document.
    """
    return await _call_sre_skill("write_post_mortem", prompt, project_id, trace_id)


SYSTEM_INSTRUCTIONS = (
    "You are a user-facing Orchestrator agent for SRE questions. You never investigate yourself: "
    "you delegate to the SRE diagnostics agent through exactly one of these tools.\n"
    "- 'list_incidents': what is failing or slow right now (fast). Use it for 'what are the latest "
    "failures?', 'is anything broken?'.\n"
    "- 'diagnose_sre': the root cause of an incident (slower). Pass the user's question as `prompt`, "
    "and `trace_id` when they name a trace.\n"
    "- 'write_post_mortem': the post-mortem of an incident. Pass `trace_id` when known.\n"
    "Pick the cheapest tool that answers the question. Answer follow-up questions about a result "
    "already in this conversation (a trace ID, a service, a timestamp) from the conversation, "
    "without calling a tool again.\n"
    "After a tool call, reply with a short summary of 2-4 sentences: what is wrong, where, and the "
    "trace ID, plus the natural next step. The user interface shows the tool's full result as a "
    "card under your reply, so do not repeat tables or reports."
)


def build_safety_policies() -> list[Any]:
    """Returns the Orchestrator's tool-call policies: deny everything, allow delegation."""
    return [deny("*"), allow("list_incidents"), allow("diagnose_sre"), allow("write_post_mortem")]


def load_agent_config(config_path: str = "agent/agent_config.json") -> LocalAgentConfig:
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    safety_policies = build_safety_policies()
    system_instructions = SYSTEM_INSTRUCTIONS

    return LocalAgentConfig(
        system_instructions=system_instructions, tools=tools, policies=safety_policies, hooks=[SreToolErrorHook()]
    )


def load_firestore_agent_config(
    conversation_id: str | None = None, config_path: str = "agent/agent_config.json"
) -> Any:
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    safety_policies = build_safety_policies()
    system_instructions = SYSTEM_INSTRUCTIONS

    if HAS_ANTIGRAVITY:
        from google.antigravity.types import SessionContinuationMode

        from agent.firestore_strategy import FirestoreAgentConfig

        return FirestoreAgentConfig(
            system_instructions=system_instructions,
            tools=tools,
            policies=safety_policies,
            hooks=[SreToolErrorHook()],
            conversation_id=conversation_id,
            # The caller picks the ID of a new conversation (so the chat can be registered
            # before the first turn runs); the same ID resumes it on every later turn.
            session_continuation_mode=SessionContinuationMode.CREATE_OR_RESUME,
        )
    else:
        config = LocalAgentConfig(
            system_instructions=system_instructions,
            tools=tools,
            policies=safety_policies,
            hooks=[SreToolErrorHook()],
        )
        config.conversation_id = conversation_id
        return config
