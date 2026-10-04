"""A minimal A2A (v1.0) client shared by the agents that call other agents.

`call_agent` resolves the remote agent's card, sends one message with request
metadata, streams the task, and returns its artifact. It works with any A2A
agent; nothing here is specific to this repository's agents.
"""

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import a2a.types as T
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.helpers import get_data_parts, get_text_parts

from sre_common.retry import retry_async

TERMINAL_FAILURES = {
    T.TaskState.TASK_STATE_FAILED,
    T.TaskState.TASK_STATE_CANCELED,
    T.TaskState.TASK_STATE_REJECTED,
}


class A2ATaskError(RuntimeError):
    """The remote agent failed the task, or finished without producing an artifact."""


@dataclass
class A2AResult:
    """What a remote agent produced: the artifact's text and structured data parts."""

    text: str = ""
    data: list[Any] = field(default_factory=list)
    task_id: str = ""
    context_id: str = ""


def _message_text(message: T.Message) -> str:
    return "".join(get_text_parts(list(message.parts)))


# Agent cards rarely change, so each one is fetched once per TTL instead of before every
# call (a round trip per hop). A transport failure drops the entry, so a redeployed agent
# is re-discovered on the next call.
CARD_TTL_SECONDS = float(os.getenv("A2A_CARD_TTL_SECONDS", "300"))
_card_cache: dict[str, tuple[float, T.AgentCard]] = {}


def clear_card_cache() -> None:
    """Forgets every cached agent card."""
    _card_cache.clear()


async def _get_card(http: httpx.AsyncClient, base_url: str, retry_connect: bool) -> T.AgentCard:
    cached = _card_cache.get(base_url)
    if cached and time.monotonic() - cached[0] < CARD_TTL_SECONDS:
        return cached[1]
    card = (
        await _resolve_card(http, base_url) if retry_connect else await A2ACardResolver(http, base_url).get_agent_card()
    )
    _card_cache[base_url] = (time.monotonic(), card)
    return card


@retry_async(max_retries=3, initial_delay=1.0)
async def _resolve_card(http: httpx.AsyncClient, base_url: str) -> T.AgentCard:
    """Fetches /.well-known/agent-card.json. The only step that is retried: once a task
    is running, re-sending the message would start the whole (non-idempotent) task again."""
    return await A2ACardResolver(http, base_url).get_agent_card()


async def call_agent(
    base_url: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    context_id: str = "",
    on_progress: Callable[[str], None] | None = None,
    timeout: float = 300.0,
    retry_connect: bool = True,
    http: httpx.AsyncClient | None = None,
) -> A2AResult:
    """Sends `text` to the A2A agent at `base_url` and returns the task's artifact.

    Progress: every WORKING status message is passed to `on_progress`, except one
    that merely repeats the final artifact (agents such as ADK's executor publish the
    result as a status message before the artifact). Each message is held until the
    next update arrives, which tells the two apart without agent-specific metadata.

    Args:
        base_url: The agent's base URL; its card is read from /.well-known/agent-card.json.
        text: The user message.
        metadata: Request metadata (e.g. {"project_id": "..."}), as the agent's card documents.
        context_id: Continue an existing conversation with the agent.
        on_progress: Called with each progress message.
        timeout: Read timeout for the stream, in seconds.
        retry_connect: Retry fetching the agent card on transient errors. Turn it off
            to fail fast when the agent is optional (e.g. absent in a local simulation).
        http: An httpx client to use (tests pass one bound to an in-process app).

    Raises:
        A2ATaskError: The task failed, was canceled or rejected, or produced no artifact.
    """
    own_client = http is None
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout))
    try:
        card = await _get_card(http, base_url, retry_connect)
        client = ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create(card)

        message = T.Message(
            role=T.Role.ROLE_USER,
            parts=[T.Part(text=text)],
            message_id=uuid.uuid4().hex,
            context_id=context_id,
        )
        request = T.SendMessageRequest(message=message)
        if metadata:
            request.metadata.update(metadata)

        result = A2AResult()
        held: str | None = None
        artifact_seen = False

        def flush_held() -> None:
            nonlocal held
            if held and on_progress:
                on_progress(held)
            held = None

        async for event in client.send_message(request):
            if event.HasField("task"):
                result.task_id, result.context_id = event.task.id, event.task.context_id
            elif event.HasField("status_update"):
                update = event.status_update
                result.task_id, result.context_id = update.task_id, update.context_id
                state = update.status.state
                if state in TERMINAL_FAILURES:
                    raise A2ATaskError(
                        f"Agent task {T.TaskState.Name(state)}: {_message_text(update.status.message) or 'no detail'}"
                    )
                if state == T.TaskState.TASK_STATE_WORKING and update.status.message.parts:
                    flush_held()
                    held = _message_text(update.status.message) or None
            elif event.HasField("artifact_update"):
                artifact = event.artifact_update.artifact
                artifact_text = "".join(get_text_parts(list(artifact.parts)))
                if held is not None and held == artifact_text:
                    held = None  # the result itself, not progress
                flush_held()
                result.text += artifact_text
                result.data.extend(get_data_parts(list(artifact.parts)))
                artifact_seen = True
            elif event.HasField("message"):
                # A direct (task-less) reply.
                result.text += _message_text(event.message)
                artifact_seen = True

        flush_held()
        if not artifact_seen:
            raise A2ATaskError("Agent finished without producing an artifact")
        return result
    except A2ATaskError:
        raise  # the agent answered; its card is fine
    except Exception:
        _card_cache.pop(base_url, None)
        raise
    finally:
        if own_client:
            await http.aclose()
