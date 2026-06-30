"""Antigravity SRE Agent Orchestrator runtime configuration.

This module defines the Orchestrator agent's configuration using the Google
Antigravity SDK. It configures system instructions to delegate SRE queries
to the SRE Sub-Agent, registers the A2A tool, and establishes safety policies.
"""

import os
import json
import logging
import asyncio
from typing import Any
import httpx
from sre_common import retry_async

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
logger = logging.getLogger("orchestrator_agent")

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
                "thinking": self.thinking,
                "tool_calls": self.tool_calls,
                "error": self.error,
                "is_complete_response": self.is_complete_response
            }

    class MockResponse:
        def __init__(self, is_diag: bool, prompt: str, conversation: Any) -> None:
            self._is_diag = is_diag
            self.prompt = prompt
            self.conversation = conversation
            self._text = ""

        @property
        def chunks(self) -> Any:
            async def _gen():
                if self._is_diag:
                    yield Thought(text="Initiating A2A connection to SRE Diagnostics Sub-Agent...")
                    await asyncio.sleep(0.5)
                    yield Thought(text="Contacting SRE Agent HTTP/SSE endpoint...")
                    await asyncio.sleep(0.5)

                    # Simulate streaming of thoughts from SRE agent
                    yield Thought(text="SRE Agent: Querying traces from project...")
                    await asyncio.sleep(0.8)
                    yield Thought(text="SRE Agent: Executing ADK workflow logic...")
                    await asyncio.sleep(0.8)

                    fallback_diagnosis = (
                        "# 🚨 Simulated Diagnostics Report\n\n"
                        "This is a simulated fallback report for local testing.\n"
                        "Anomalous trace found with latency spiked in child database spans."
                    )
                    # Delegate to the real in-process SRE workflow so the offline
                    # simulation surfaces the full cascade + post-mortem report,
                    # just like the deployed orchestrator calling its diagnose_sre tool.
                    if os.getenv("MOCK_GCP", "false").lower() == "true":
                        try:
                            diagnosis = await diagnose_sre(self.prompt)
                        except Exception as diag_err:
                            logger.error(f"Mock orchestrator failed to run diagnose_sre: {diag_err}")
                            diagnosis = fallback_diagnosis
                    else:
                        diagnosis = fallback_diagnosis
                    self._text = diagnosis
                    
                    yield Thought(text="Orchestration complete. Streaming report...")
                    await asyncio.sleep(0.5)

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
                        thinking="Orchestration complete. Streaming report...",
                        tool_calls=[
                            {"name": "diagnose_sre", "args": {"prompt": self.prompt}}
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
                content=prompt
            )
            self.conversation._steps.append(user_step)

            class MockResponseWrapper:
                def __init__(self, is_diag: bool, prompt: str, conversation: Any) -> None:
                    self.response = MockResponse(is_diag, prompt, conversation)
                @property
                def chunks(self):
                    return self.response.chunks
                async def text(self):
                    # Consume the chunks to build the full text
                    async for chunk in self.response.chunks:
                        pass
                    return self.response._text
                async def cancel(self):
                    pass

            return MockResponseWrapper(is_diag, prompt, self.conversation)

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

    def deny(target: str) -> Any: return f"deny:{target}"
    def allow(target: str) -> Any: return f"allow:{target}"
    def ask_user(target: str, *, handler: Any = None) -> Any: return f"ask_user:{target}"
    class OnToolErrorHook: pass
    class HookContext: pass


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


@retry_async(max_retries=3, initial_delay=2.0)
async def _post_to_sre_agent(url: str, payload: dict[str, Any]) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=60.0)
        response.raise_for_status()
        
        # Consume SSE stream events to extract final report
        accumulated_report = ""
        for line in response.iter_lines():
            if line.startswith("data: "):
                try:
                    event_data = json.loads(line[6:])
                    if event_data.get("type") == "done":
                        accumulated_report = event_data.get("response", "")
                        break
                    elif event_data.get("type") == "chunk":
                        accumulated_report += event_data.get("text", "")
                except Exception:
                    pass
        return accumulated_report


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
    if os.getenv("MOCK_GCP", "false").lower() == "true":
        logger.info("MOCK_GCP is true. Running SRE sub-agent diagnostics workflow in-process.")
        try:
            from sre_agent.gcp_tools import query_traces
            from sre_agent.sre_workflow import run_sre_diagnostics
            resolved_project = project_id or os.environ.get("GCP_PROJECT") or "simulation-project-123"
            traces_json = await query_traces(project_id=resolved_project, limit=10)
            return await run_sre_diagnostics(traces_json=traces_json, project_id=resolved_project)
        except Exception as mock_err:
            logger.error(f"Failed to run in-process mock diagnostics: {mock_err}")
            return (
                "# 🚨 Simulated Diagnostics Report (In-Process Fallback)\n\n"
                "This is a simulated fallback report for local testing.\n"
                "Anomalous trace found with latency spiked in child database spans."
            )

    sre_agent_url = os.getenv("SRE_AGENT_URL", "http://sre-agent:8080")
    url = f"{sre_agent_url}/v1/agents/sre/messages"
    payload = {
        "prompt": prompt,
        "project_id": project_id,
        "refresh": refresh
    }
    
    logger.info(f"Orchestrating A2A POST to SRE Agent: {url}")
    try:
        return await _post_to_sre_agent(url, payload)
    except Exception as e:
        logger.error(f"Failed to communicate with SRE sub-agent: {e}")
        return f"Error: Failed to contact SRE Sub-Agent after retries: {str(e)}"


def load_agent_config(config_path: str = "agent/agent_config.json") -> LocalAgentConfig:
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    safety_policies = [
        deny("*"),
        allow("diagnose_sre")
    ]

    system_instructions = (
        "You are a user-facing Orchestrator agent.\n"
        "Your role is to assist the user. If the user requests SRE incident diagnostics, "
        "trace analysis, error log reviews, or database debugging, delegate the task "
        "immediately to the SRE diagnostics agent using the 'diagnose_sre' tool and present "
        "the final report to the user. Do not attempt to run diagnostics yourself."
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
    tools: list[Any] = []
    tools.extend(registry.get_tools())

    safety_policies = [
        deny("*"),
        allow("diagnose_sre")
    ]

    system_instructions = (
        "You are a user-facing Orchestrator agent.\n"
        "Your role is to assist the user. If the user requests SRE incident diagnostics, "
        "trace analysis, error log reviews, or database debugging, delegate the task "
        "immediately to the SRE diagnostics agent using the 'diagnose_sre' tool and present "
        "the final report to the user. Do not attempt to run diagnostics yourself."
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
        config = LocalAgentConfig(
            system_instructions=system_instructions,
            tools=tools,
            policies=safety_policies,
            hooks=[SreToolErrorHook()],
        )
        config.conversation_id = conversation_id
        return config
