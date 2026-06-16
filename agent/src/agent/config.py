"""Antigravity SRE Agent runtime configuration.

This module defines the SRE agent's configuration using the Google
Antigravity SDK. It configures the system prompt, registers custom tools,
parses the dynamic MCP configuration, and establishes strict safety policies.
"""

import os
import json
import logging
from typing import Any
from skills.sre_incident_solver.registry import registry, register_tool
from skills.sre_incident_solver.sre_workflow import run_sre_diagnostics

# Fail-safe OpenTelemetry imports for tracer initialization
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

# Setup logging
logger = logging.getLogger("sre_agent")

# Resilient imports for google-antigravity
try:
    from google.antigravity import Agent, LocalAgentConfig
    from google.antigravity.hooks.policy import deny, allow, ask_user
    from google.antigravity.hooks.hooks import OnToolErrorHook, HookContext
    from google.antigravity.types import Text, Thought, ToolCall, ToolResult
    HAS_ANTIGRAVITY = "GEMINI_API_KEY" in os.environ
except ImportError:
    HAS_ANTIGRAVITY = False

# Global database to persist mock session history in local simulation mode
MOCK_HISTORY_DB: dict[str, list[dict[str, Any]]] = {}

if not HAS_ANTIGRAVITY:
    logger.warning("google-antigravity is not active or GEMINI_API_KEY is missing. Using simulated agent config fallbacks.")

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
                "content_delta": self.content_delta,
                "thinking": self.thinking,
                "thinking_delta": self.thinking_delta,
                "tool_calls": self.tool_calls,
                "error": self.error,
                "is_complete_response": self.is_complete_response,
                "structured_output": self.structured_output,
                "usage_metadata": self.usage_metadata,
            }

    class MockConversation:
        def __init__(self, conversation_id: str | None = None) -> None:
            self.conversation_id = conversation_id or "mock-conversation-id-123"
            self._steps: list[Any] = []

        @property
        def history(self) -> list[Any]:
            return self._steps

    class Agent:  # type: ignore
        """Mock Agent context manager for local resilience."""
        def __init__(self, config: Any) -> None:
            self.config = config
            self._conversation_obj = None

        async def __aenter__(self) -> "Agent":
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        @property
        def conversation(self) -> MockConversation:
            if self._conversation_obj is None:
                self._conversation_obj = MockConversation(conversation_id=self.conversation_id)
            return self._conversation_obj

        async def chat(self, prompt: str) -> Any:
            """Simulates the agent chat response."""
            is_diag = any(x in prompt.lower() for x in ("traces", "latency", "errors"))
            import asyncio

            # Append user step
            user_step = MockStep(
                step_index=len(self.conversation._steps),
                type="TEXT_RESPONSE",
                source="USER",
                target="TARGET_UNSPECIFIED",
                status="DONE",
                content=prompt
            )
            self.conversation._steps.append(user_step)

            class MockResponse:
                def __init__(self, is_diag: bool, prompt: str, conversation: Any) -> None:
                    self.is_diag = is_diag
                    self.prompt = prompt
                    self.conversation = conversation
                    self._text = None

                async def text(self) -> str:
                    if self._text is not None:
                        return self._text
                    if self.is_diag:
                        from skills.sre_incident_solver.gcp_tools import query_traces
                        traces = await query_traces()
                        self._text = await run_sre_diagnostics(traces)
                    else:
                        self._text = f"Simulation mode: analyzed prompt '{self.prompt}'."
                    return self._text

                async def cancel(self) -> None:
                    """Cancels mock response generation."""
                    pass

                @property
                def chunks(self) -> Any:
                    async def _gen() -> Any:
                        if self.is_diag:
                            # 1. ToolCall for query_traces
                            yield ToolCall(name="query_traces", args={})
                            await asyncio.sleep(0.5)
                            from skills.sre_incident_solver.gcp_tools import query_traces
                            traces = await query_traces()
                            # 2. ToolResult for query_traces
                            yield ToolResult(name="query_traces", result=traces)
                            await asyncio.sleep(0.5)

                            # 3. ToolCall for run_diagnostics_workflow
                            yield ToolCall(name="run_diagnostics_workflow", args={"traces_data": traces})
                            await asyncio.sleep(1.0)
                            diagnosis = await run_sre_diagnostics(traces)
                            self._text = diagnosis
                            # 4. ToolResult for run_diagnostics_workflow
                            yield ToolResult(name="run_diagnostics_workflow", result=diagnosis)
                            await asyncio.sleep(0.5)

                            # 5. Thought chunk explaining findings
                            yield Thought(text="Diagnostics workflow complete. Preparing report details...")
                            await asyncio.sleep(0.5)

                            # 6. Stream the final diagnosis report text
                            words = diagnosis.split(" ")
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
                                thinking="Diagnostics workflow complete. Preparing report details...",
                                tool_calls=[
                                    {"name": "query_traces", "args": {}},
                                    {"name": "run_diagnostics_workflow", "args": {"traces_data": "<omitted>"}}
                                ]
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
                                thinking="Simulating basic greeting response..."
                            )
                            self.conversation._steps.append(model_step)
                    return _gen()

            return MockResponse(is_diag, prompt, self.conversation)

        @property
        def conversation_id(self) -> str | None:
            """Returns simulated conversation ID."""
            if not getattr(self.config, "conversation_id", None):
                import uuid
                self.config.conversation_id = f"mock-{uuid.uuid4().hex}"
            return self.config.conversation_id


    class LocalAgentConfig:  # type: ignore
        """Mock LocalAgentConfig for local resilience."""
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

    # Mock safety policies
    def deny(target: str) -> Any: return f"deny:{target}"
    def allow(target: str) -> Any: return f"allow:{target}"
    def ask_user(target: str, *, handler: Any = None) -> Any: return f"ask_user:{target}"
    class OnToolErrorHook: pass  # type: ignore
    class HookContext: pass  # type: ignore


class SreToolErrorHook(OnToolErrorHook):
    """Custom hook to handle and recover from SRE tool execution errors."""

    async def run(self, context: HookContext, data: Exception) -> str | None:
        """Intercepts tool errors and returns a helpful recovery message.

        Args:
            context: The execution context of the hook.
            data: The exception raised by the tool.

        Returns:
            A string containing system recovery instructions or None.
        """
        logger.error(f"SRE Agent Tool Error: {data}")
        # Handle GCP API permission denied errors gracefully
        if "PermissionDenied" in str(data) or "Forbidden" in str(data):
            return (
                "[System: Permission Denied. The SRE agent service account lacks the required "
                "GCP IAM permissions. Please verify that SRE Agent service account has "
                "roles/logging.viewer and roles/cloudtrace.user assigned, or run in local "
                "simulation mode by setting the environment variable MOCK_GCP=true.]"
            )
        return None


@register_tool
async def run_diagnostics_workflow(traces_data: str, project_id: str | None = None) -> str:
    """Invokes the ADK multi-agent workflow to analyze trace spans and correlated logs.

    Args:
        traces_data: A JSON string containing trace summaries.
        project_id: The GCP Project ID. If None, uses default configuration.

    Returns:
        A markdown-formatted SRE incident diagnosis report.
    """
    return await run_sre_diagnostics(traces_data, project_id)


async def cli_approval_handler(context: Any) -> bool:
    """Prompt the user for approval before running sensitive tools.

    If running in a non-interactive shell (like CI tests or Cloud Run),
    automatically allows the call to prevent hanging.
    """
    import sys
    tool_name = getattr(context, "name", "unknown_tool")
    args = getattr(context, "args", {})

    logger.warning(f"Security Alert: Agent wants to run sensitive tool '{tool_name}' with args {args}")

    # Check if we are in a non-interactive environment (CI, test, or Cloud Run)
    is_interactive = sys.stdin.isatty() and os.getenv("NON_INTERACTIVE", "false").lower() not in ("true", "1")

    if not is_interactive:
        logger.info(f"Non-interactive environment detected. Auto-approving execution of '{tool_name}'.")
        return True

    print(f"\n⚠️  [SECURITY GATING] SRE Agent requests permission to run '{tool_name}'")
    print(f"Arguments: {args}")
    try:
        user_input = input("Approve tool execution? (y/N): ")
        return user_input.strip().lower() in ("y", "yes")
    except Exception as e:
        logger.error(f"Failed to read user input, denying execution: {e}")
        return False


def load_agent_config(config_path: str = "agent/agent_config.json") -> LocalAgentConfig:
    """Loads safety configurations, tools, and dynamic MCP servers.

    Args:
        config_path: Path to the agent configuration JSON file.

    Returns:
        A LocalAgentConfig instance configured for SRE diagnostics.
    """
    # Initialize OpenTelemetry if available and not in mock mode
    IS_MOCK_ENV = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")
    if HAS_OTEL and not IS_MOCK_ENV:
        try:
            # Check if tracer provider is already set
            try:
                trace.get_tracer_provider()
            except Exception:
                provider = TracerProvider()
                exporter = CloudTraceSpanExporter()
                processor = BatchSpanProcessor(exporter)
                provider.add_span_processor(processor)
                trace.set_tracer_provider(provider)
                logger.info("Successfully initialized agent OpenTelemetry tracer provider.")
        except Exception as e:
            logger.error(f"Failed to initialize agent OpenTelemetry: {e}")

    # 1. Gather all registered python tools
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    # 2. Dynamically load MCP configurations from agent_config.json
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            mcp_servers = config_data.get("mcp_servers", {})
            for name, details in mcp_servers.items():
                if details.get("enabled", False):
                    logger.info(f"Dynamically loading MCP server configuration: {name}")
                    tools.append({
                        "type": "mcp",
                        "name": name,
                        "command": details.get("command"),
                        "args": details.get("args", []),
                        "env": details.get("env", {})
                    })
        except Exception as e:
            logger.error(f"Failed to load agent configuration file: {e}")

    # 3. Setup safety policies (deny-by-default, allow specific tools, ask before writing)
    safety_policies = [
        deny("*"),  # Deny all commands/actions by default
        allow("query_traces"),
        allow("get_trace_details"),
        allow("query_logs_by_trace"),
        allow("run_diagnostics_workflow"),
        allow("query_logs"),
        ask_user("run_command", handler=cli_approval_handler)  # Require confirmation for shell commands
    ]

    system_instructions = (
        "You are an expert Google Cloud SRE agent specialized in distributed system debugging. "
        "You have access to low-level telemetry tools (traces, log search) and a high-level diagnostics workflow tool:\n"
        "1. For general trace-based investigation: Retrieve recent traces using 'query_traces', "
        "then pass the trace summaries to 'run_diagnostics_workflow' to execute a multi-agent diagnostic "
        "analysis that automatically correlates traces and logs to produce a root cause report.\n"
        "2. For log-based investigation: Use 'query_logs' to perform custom log searches (e.g. searching for error keywords, "
        "specific services, or severity levels). You can find correlated trace IDs within the logs to perform deeper tracing "
        "using 'get_trace_details' and 'query_logs_by_trace'.\n"
        "3. For self diagnostics: Use 'query_logs' with query 'sre-agent' to fetch and diagnose your own agent execution logs.\n"
        "4. Obtain more details: If the user request is vague or ambiguous (e.g. missing product names or service details), "
        "ask the user for clarifying details (such as service name or product area of interest) to refine your diagnostic queries.\n"
        "Always present your final diagnosis or analysis results clearly in markdown."
    )

    return LocalAgentConfig(
        system_instructions=system_instructions,
        tools=tools,
        policies=safety_policies,
        hooks=[SreToolErrorHook()]
    )


def load_firestore_agent_config(
    conversation_id: str | None = None,
    config_path: str = "agent/agent_config.json"
) -> Any:
    """Loads safety configurations, tools, dynamic MCP servers, and uses Firestore persistence.

    Args:
        conversation_id: Optional conversation ID to resume.
        config_path: Path to the agent configuration JSON file.

    Returns:
        A FirestoreAgentConfig (or mock equivalent) instance.
    """
    # 1. Gather all registered python tools
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    # 2. Dynamically load MCP configurations from agent_config.json
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            mcp_servers = config_data.get("mcp_servers", {})
            for name, details in mcp_servers.items():
                if details.get("enabled", False):
                    logger.info(f"Dynamically loading MCP server configuration: {name}")
                    tools.append({
                        "type": "mcp",
                        "name": name,
                        "command": details.get("command"),
                        "args": details.get("args", []),
                        "env": details.get("env", {})
                    })
        except Exception as e:
            logger.error(f"Failed to load agent configuration file: {e}")

    # 3. Setup safety policies (deny-by-default, allow specific tools, ask before writing)
    safety_policies = [
        deny("*"),  # Deny all commands/actions by default
        allow("query_traces"),
        allow("get_trace_details"),
        allow("query_logs_by_trace"),
        allow("run_diagnostics_workflow"),
        ask_user("run_command", handler=cli_approval_handler)  # Require confirmation for shell commands
    ]

    system_instructions = (
        "You are an expert Google Cloud SRE agent specialized in distributed system debugging. "
        "You have access to both low-level telemetry tools and a high-level diagnostics workflow tool:\n"
        "1. For general incident investigation: Retrieve recent traces using 'query_traces', "
        "then pass the trace summaries to 'run_diagnostics_workflow' to execute a multi-agent diagnostic "
        "analysis that automatically correlates traces and logs to produce a root cause report.\n"
        "2. For targeted queries or detailed troubleshooting: Use the low-level tools 'get_trace_details' "
        "and 'query_logs_by_trace' to inspect specific traces and logs directly.\n"
        "Always present your final diagnosis or analysis results clearly in markdown."
    )

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
        # Fallback in local mock mode without Antigravity library
        config = LocalAgentConfig(
            system_instructions=system_instructions,
            tools=tools,
            policies=safety_policies,
            hooks=[SreToolErrorHook()],
        )
        config.conversation_id = conversation_id
        return config

