"""Antigravity SRE Agent Orchestrator runtime configuration.

This module defines the Orchestrator agent's configuration using the Google
Antigravity SDK. It configures system instructions to delegate SRE queries
to the SRE Sub-Agent, registers the A2A tool, and establishes safety policies.
"""

import asyncio
import contextvars
import logging
import os
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
        def __init__(self, is_diag: bool, prompt: str, conversation: Any, policies: list[Any]) -> None:
            self._is_diag = is_diag
            self.prompt = prompt
            self.conversation = conversation
            self.policies = policies
            self._text = ""

        @property
        def chunks(self) -> Any:
            async def _gen():
                if self._is_diag:
                    # Stand-in for the model deciding to call `diagnose_sre`. The call
                    # still goes through the configured policies, exactly as it would
                    # in the real Antigravity harness.
                    yield ToolCall(name="diagnose_sre", args={"prompt": self.prompt})
                    decision = evaluate_mock_policy(self.policies, "diagnose_sre")
                    if decision == "allow":
                        try:
                            diagnosis = await diagnose_sre(self.prompt)
                        except Exception as diag_err:
                            logger.error(f"Mock orchestrator failed to run diagnose_sre: {diag_err}")
                            diagnosis = f"Error: diagnose_sre failed: {diag_err!s}"
                    else:
                        logger.warning(f"Policy decision for diagnose_sre is '{decision}'. Tool call blocked.")
                        diagnosis = (
                            f"The `diagnose_sre` tool call was blocked by the safety policy (decision: {decision})."
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
                        thinking="Delegated SRE diagnostics to the diagnose_sre tool.",
                        tool_calls=[{"name": "diagnose_sre", "args": {"prompt": self.prompt}}],
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
            # Check if SRE diagnostics keyword is present
            is_diag = any(x in prompt.lower() for x in ("diagnose", "error", "trace", "latency", "sre"))

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
                def __init__(self, is_diag: bool, prompt: str, conversation: Any, policies: list[Any]) -> None:
                    self.response = MockResponse(is_diag, prompt, conversation, policies)

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

            return MockResponseWrapper(is_diag, prompt, self.conversation, self.config.policies)

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


@register_tool
async def diagnose_sre(prompt: str, project_id: str | None = None, refresh: bool = False) -> str:
    """Delegates complex SRE diagnostics, trace correlation, and log analysis to the SRE Sub-Agent.

    Args:
        prompt: The SRE diagnostic prompt explaining the issue or symptoms.
        project_id: The GCP Project ID. If None, uses default project.
        refresh: Set True to force a fresh infrastructure rescan/discovery.

    Returns:
        A markdown-formatted SRE incident diagnosis report.
    """
    sre_agent_url = os.getenv("SRE_AGENT_URL")
    mock_mode = os.getenv("MOCK_GCP", "false").lower() == "true"

    # Standalone simulation (simulate_incident.py): no sub-agent service is
    # running, so run the same workflow in-process. When SRE_AGENT_URL is set
    # (docker-compose, Cloud Run) always delegate over A2A.
    if mock_mode and not sre_agent_url:
        logger.info("MOCK_GCP is true and SRE_AGENT_URL is unset. Running SRE workflow in-process.")
        _emit_progress("Running the SRE diagnostics workflow in-process (simulation mode)...")
        try:
            from sre_agent.gcp_tools import query_traces
            from sre_agent.sre_workflow import run_sre_diagnostics

            resolved_project = project_id or os.environ.get("GCP_PROJECT") or "simulation-project-123"
            traces_json = await query_traces(project_id=resolved_project, limit=10)
            report = await run_sre_diagnostics(traces_json=traces_json, project_id=resolved_project)
        except Exception as mock_err:
            logger.error(f"Failed to run in-process mock diagnostics: {mock_err}")
            report = f"Error: in-process SRE diagnostics failed: {mock_err!s}"
    else:
        # The SRE engine is an A2A agent: its card at /.well-known/agent-card.json says how
        # to reach it. Progress arrives as task status updates, the report as the artifact.
        base_url = sre_agent_url or "http://sre-agent:8080"
        sink = diagnosis_sink.get()
        logger.info(f"Delegating to the SRE agent over A2A: {base_url}")
        _emit_progress("Contacting the SRE diagnostics sub-agent over A2A...")
        try:
            result = await call_agent(
                base_url,
                prompt,
                {"project_id": project_id or os.environ.get("GCP_PROJECT", ""), "refresh": refresh},
                context_id=sink.context_id if sink else "",
                on_progress=_emit_progress,
            )
            report = result.text
        except Exception as e:
            logger.error(f"Failed to communicate with SRE sub-agent: {e}")
            report = f"Error: Failed to contact SRE Sub-Agent: {e!s}"

    sink = diagnosis_sink.get()
    if sink is not None:
        sink.report = report
    return report


SYSTEM_INSTRUCTIONS = (
    "You are a user-facing Orchestrator agent.\n"
    "Your role is to assist the user. If the user requests SRE incident diagnostics, "
    "trace analysis, error log reviews, or database debugging, delegate the task "
    "immediately to the SRE diagnostics agent using the 'diagnose_sre' tool and present "
    "the final report to the user verbatim, including any post-mortem section. "
    "Do not attempt to run diagnostics yourself."
)


def build_safety_policies() -> list[Any]:
    """Returns the Orchestrator's tool-call policies: deny everything, allow delegation."""
    return [deny("*"), allow("diagnose_sre")]


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
        from agent.firestore_strategy import FirestoreAgentConfig

        return FirestoreAgentConfig(
            system_instructions=system_instructions,
            tools=tools,
            policies=safety_policies,
            hooks=[SreToolErrorHook()],
            conversation_id=conversation_id,
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
