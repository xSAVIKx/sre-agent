"""Antigravity SRE Agent runtime configuration.

This module defines the SRE agent's configuration using the Google
Antigravity SDK. It configures the system prompt, registers custom tools,
parses the dynamic MCP configuration, and establishes strict safety policies.
"""

import os
import json
import logging
from typing import Any
from .registry import registry, register_tool
from .sre_workflow import run_sre_diagnostics

# Setup logging
logger = logging.getLogger("sre_agent")

# Resilient imports for google-antigravity
try:
    from google.antigravity import Agent, LocalAgentConfig
    from google.antigravity.hooks.policy import deny, allow, ask_user
    from google.antigravity.hooks.hooks import OnToolErrorHook, HookContext
    HAS_ANTIGRAVITY = "GEMINI_API_KEY" in os.environ
except ImportError:
    HAS_ANTIGRAVITY = False

if not HAS_ANTIGRAVITY:
    logger.warning("google-antigravity is not active or GEMINI_API_KEY is missing. Using simulated agent config fallbacks.")

    class Agent:  # type: ignore
        """Mock Agent context manager for local resilience."""
        def __init__(self, config: Any) -> None:
            self.config = config

        async def __aenter__(self) -> "Agent":
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        async def chat(self, prompt: str) -> Any:
            """Simulates the agent chat response."""
            class MockResponse:
                async def text(self) -> str:
                    if "traces" in prompt.lower() or "latency" in prompt.lower() or "errors" in prompt.lower():
                        from .gcp_tools import query_traces
                        traces = await query_traces()
                        diagnosis = await run_sre_diagnostics(traces)
                        return diagnosis
                    return f"Simulation mode: analyzed prompt '{prompt}'."
            return MockResponse()

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
    tool_name = getattr(context, "tool", "unknown_tool")
    args = getattr(context, "arguments", {})

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


def load_agent_config(config_path: str = "agent_config.json") -> LocalAgentConfig:
    """Loads safety configurations, tools, and dynamic MCP servers.

    Args:
        config_path: Path to the agent configuration JSON file.

    Returns:
        A LocalAgentConfig instance configured for SRE diagnostics.
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
        "Your task is to analyze system failures by running the 'query_traces' tool to inspect trace summaries, "
        "finding the failing trace ID, fetching detailed spans via 'get_trace_details', querying logs "
        "correlated with that trace ID via 'query_logs_by_trace', and executing the "
        "'run_diagnostics_workflow' to generate a root-cause diagnosis. Always present your final diagnosis "
        "clearly in markdown."
    )

    return LocalAgentConfig(
        system_instructions=system_instructions,
        tools=tools,
        policies=safety_policies,
        hooks=[SreToolErrorHook()]
    )
