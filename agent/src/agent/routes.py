"""FastAPI Route Definitions for Orchestrator Agent.

Exposes endpoints for health check, session management, trace proxy, and stateful A2A chat orchestration.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from agent.config import (
    HAS_ANTIGRAVITY,
    Agent,
    DiagnosisSink,
    diagnose_sre,
    diagnosis_sink,
    load_firestore_agent_config,
)
from sre_common import otel_trace, retry_async

logger = logging.getLogger("orchestrator_agent.routes")

router = APIRouter()


class DiagnoseRequest(BaseModel):
    """Pydantic model representing a diagnostic request."""

    prompt: str
    project_id: str | None = None


class DiagnoseResponse(BaseModel):
    """Pydantic model representing the agent diagnostics response."""

    status: str
    result: str


class SurfaceAction(BaseModel):
    """An A2UI action the user triggered on a surface (e.g. a button), as the renderer reports it."""

    name: str
    context: dict[str, Any] = {}
    surfaceId: str = ""  # the A2UI field name


# What each A2UI action the SRE agent's surfaces can send asks the Orchestrator. The
# action becomes an ordinary chat turn, so it goes through the agent and its policy.
ACTION_PROMPTS = {
    "diagnose_incident": "Diagnose trace {traceId}.",
    "write_post_mortem": "Write the post-mortem for trace {traceId}.",
}
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


def action_prompt(action: SurfaceAction) -> str:
    """The chat prompt for a surface action. Rejects unknown actions and malformed trace IDs."""
    template = ACTION_PROMPTS.get(action.name)
    trace_id = str(action.context.get("traceId", ""))
    if template is None or not _TRACE_ID.match(trace_id):
        raise ValueError(f"Unsupported surface action {action.name!r}")
    return template.format(traceId=trace_id)


class ChatRequest(BaseModel):
    """Pydantic model representing a stateful chat request: a prompt, or a surface action."""

    prompt: str = ""
    action: SurfaceAction | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    refresh: bool = False

    @model_validator(mode="after")
    def _prompt_from_action(self) -> "ChatRequest":
        if self.action is not None:
            self.prompt = action_prompt(self.action)
        if not self.prompt.strip():
            raise ValueError("Either a prompt or an action is required")
        return self


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "healthy", "sdk_loaded": str(HAS_ANTIGRAVITY)}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Returns a 204 No Content for favicon requests."""
    return Response(status_code=204)


@router.post("/diagnose")
@otel_trace("routes.diagnose")
async def diagnose(request: DiagnoseRequest) -> DiagnoseResponse:
    """Non-interactive diagnostics: runs the Orchestrator's one allowed tool, `diagnose_sre`."""
    logger.info(f"Received SRE diagnostics request: {request.prompt}")
    result = await diagnose_sre(request.prompt, project_id=request.project_id)
    if result.startswith("Error:"):
        raise HTTPException(status_code=502, detail=result)
    return DiagnoseResponse(status="success", result=result)


@router.get("/sessions")
async def get_sessions():
    """Retrieve all available diagnostic sessions with metadata."""
    try:
        from google.cloud import firestore

        db = firestore.AsyncClient()
        collection = db.collection("agent_sessions")
        docs = (
            await collection.select(["conversation_id", "updated_at", "prompt"])
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(50)
            .get()
        )
        sessions = []
        for doc in docs:
            data = doc.to_dict()
            updated_at = data.get("updated_at")
            updated_at_str = updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)
            sessions.append(
                {
                    "conversation_id": doc.id,
                    "prompt": data.get("prompt") or "Untitled Session",
                    "updated_at": updated_at_str,
                }
            )
        return sessions
    except Exception as e:
        logger.warning(f"Using mock database retrieval for sessions: {e}")
        from agent.config import MOCK_HISTORY_DB

        sessions = []
        for conv_id, steps in MOCK_HISTORY_DB.items():
            sessions.append(
                {
                    "conversation_id": conv_id,
                    "prompt": steps[0]["content"] if steps else "Untitled Session",
                    "updated_at": datetime.datetime.now().isoformat(),
                }
            )
        return sessions


class RenameSessionRequest(BaseModel):
    title: str


@router.put("/sessions/{conversation_id}/title")
async def rename_session(conversation_id: str, request: RenameSessionRequest):
    """Rename/modify the title of an existing session."""
    try:
        from google.cloud import firestore

        db = firestore.AsyncClient()
        doc_ref = db.collection("agent_sessions").document(conversation_id)
        await doc_ref.set({"prompt": request.title}, merge=True)
        return {"status": "success", "conversation_id": conversation_id, "title": request.title}
    except Exception as e:
        logger.warning(f"Using mock database rename fallback: {e}")
        from agent.config import MOCK_HISTORY_DB

        if conversation_id in MOCK_HISTORY_DB:
            return {"status": "success", "conversation_id": conversation_id, "title": request.title}
        raise HTTPException(status_code=404, detail="Session not found") from e


@router.delete("/sessions/{conversation_id}")
async def delete_session(conversation_id: str):
    """Delete an existing session."""
    try:
        from google.cloud import firestore

        db = firestore.AsyncClient()
        doc_ref = db.collection(SESSIONS_COLLECTION).document(conversation_id)
        # Firestore doesn't cascade: remove the transcript turns first.
        async for turn in doc_ref.collection(TURNS_COLLECTION).stream():
            await turn.reference.delete()
        await doc_ref.delete()
        return {"status": "success", "conversation_id": conversation_id}
    except Exception as e:
        logger.warning(f"Using mock database delete fallback: {e}")
        from agent.config import MOCK_HISTORY_DB

        if conversation_id in MOCK_HISTORY_DB:
            del MOCK_HISTORY_DB[conversation_id]
        return {"status": "success", "conversation_id": conversation_id}


SESSIONS_COLLECTION = "agent_sessions"
TURNS_COLLECTION = "turns"


def _turn_entries(
    prompt: str,
    reply: str,
    thinking: list[str],
    tool_calls: list[str],
    rendered: str,
    a2ui: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The compact user/model pair one chat turn adds to the transcript.

    Only what the UI replays is kept; the raw SDK steps are far too large to store
    (one diagnosis is ~90 streamed steps). Under the reply the UI shows the SRE
    skill's result: its A2UI surface (`a2ui`), or else its Markdown (`rendered`,
    stored only when it differs from the reply).
    """
    user = {"source": "USER", "content": prompt}
    model: dict[str, Any] = {"source": "MODEL", "content": reply}
    if thinking:
        model["thinking"] = "\n".join(thinking)
    if tool_calls:
        model["tool_calls"] = [{"name": name} for name in tool_calls]
    if a2ui:
        model["a2ui"] = a2ui
    elif rendered and rendered != reply:
        model["rendered"] = rendered
    return user, model


# The Antigravity SDK rejects conversation IDs shorter than this.
MIN_CONVERSATION_ID_LENGTH = 32


def _resolve_conversation_id(requested: str | None) -> str:
    """The conversation ID for this turn: the requested one, or a new one for a new chat.

    Legacy sessions (e.g. Firestore auto-IDs from before the agent persisted its own
    state) have IDs the SDK cannot resume; they continue as a new conversation.
    """
    if requested and len(requested) >= MIN_CONVERSATION_ID_LENGTH:
        return requested
    if requested:
        logger.info(f"Conversation ID {requested!r} cannot be resumed by the agent; starting a new conversation.")
    return uuid.uuid4().hex


async def _register_session(conv_id: str, prompt: str) -> None:
    """Creates the session record of a new chat before its first turn runs."""
    try:
        if not HAS_ANTIGRAVITY:
            from agent.config import MOCK_HISTORY_DB

            MOCK_HISTORY_DB.setdefault(conv_id, [])
            return
        from google.cloud import firestore

        db = firestore.AsyncClient()
        await (
            db.collection(SESSIONS_COLLECTION)
            .document(conv_id)
            .set({"conversation_id": conv_id, "prompt": prompt, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
        )
    except Exception:
        # The chat still works; it just won't be listed until its first turn is saved.
        logger.exception(f"Failed to register new conversation {conv_id}")


@dataclass
class _Turn:
    """One chat turn in the transcript, saved before it runs and completed when it ends."""

    conv_id: str
    prompt: str
    # The Firestore turn document, or (simulation) the placeholder entry in MOCK_HISTORY_DB.
    ref: Any


# A turn whose answer was never saved (e.g. the instance stopped) shows as failed after this.
STALE_TURN = datetime.timedelta(minutes=15)


async def _start_turn(conv_id: str, prompt: str, user: dict[str, Any]) -> _Turn:
    """Saves the user's message as a running turn, before the agent answers.

    Each turn is its own document under agent_sessions/{id}/turns, so a long
    conversation never approaches Firestore's 1 MiB document limit - the session
    document itself also holds the Antigravity harness snapshot. A running turn is
    replayed as "still working", so a browser that lost the connection can wait for it.
    """
    if not HAS_ANTIGRAVITY:
        from agent.config import MOCK_HISTORY_DB

        placeholder = {"source": "MODEL", "pending": True}
        MOCK_HISTORY_DB.setdefault(conv_id, []).extend([user, placeholder])
        return _Turn(conv_id, prompt, placeholder)

    from google.cloud import firestore

    db = firestore.AsyncClient()
    ref = db.collection(SESSIONS_COLLECTION).document(conv_id).collection(TURNS_COLLECTION).document()
    await ref.set({"created_at": firestore.SERVER_TIMESTAMP, "user": user, "status": "running"})
    return _Turn(conv_id, prompt, ref)


async def _finish_turn(turn: _Turn, model: dict[str, Any]) -> None:
    """Saves the answer of a running turn, and lists the chat as updated."""
    if not HAS_ANTIGRAVITY:
        turn.ref.clear()
        turn.ref.update(model)
        return

    from google.cloud import firestore

    await turn.ref.update({"model": model, "status": "done"})
    session = turn.ref.parent.parent
    existing = await session.get()
    update: dict[str, Any] = {"conversation_id": turn.conv_id, "updated_at": firestore.SERVER_TIMESTAMP}
    if not (existing.exists and (existing.to_dict() or {}).get("prompt")):
        update["prompt"] = turn.prompt
    await session.set(update, merge=True)


def _model_entry(turn: dict[str, Any]) -> dict[str, Any] | None:
    """The answer of a saved turn: the model entry, "still working", or a failure if it stalled."""
    if turn.get("model"):
        return turn["model"]
    if turn.get("status") != "running":
        return None
    created = turn.get("created_at")
    if isinstance(created, datetime.datetime) and datetime.datetime.now(datetime.UTC) - created > STALE_TURN:
        return {"source": "MODEL", "content": "Error: the agent did not finish this answer.", "error": True}
    return {"source": "MODEL", "pending": True}


@router.get("/sessions/{conversation_id}/history")
async def get_session_history(conversation_id: str):
    """Retrieve the conversation transcript for a specific session."""
    try:
        from google.cloud import firestore

        db = firestore.AsyncClient()
        session = db.collection(SESSIONS_COLLECTION).document(conversation_id)
        turns = await session.collection(TURNS_COLLECTION).order_by("created_at").get()
        history: list[dict[str, Any]] = []
        for turn in turns:
            data = turn.to_dict() or {}
            history.extend(entry for entry in (data.get("user"), _model_entry(data)) if entry)
        if not history:
            # Sessions saved before transcripts moved to a subcollection keep them inline.
            doc = await session.get()
            history = (doc.to_dict() or {}).get("history", []) if doc.exists else []
        return {"conversation_id": conversation_id, "history": history}
    except Exception as e:
        logger.warning(f"Using mock database history fallback: {e}")
        from agent.config import MOCK_HISTORY_DB

        return {
            "conversation_id": conversation_id,
            "history": MOCK_HISTORY_DB.get(conversation_id, []),
        }


@retry_async(max_retries=3, initial_delay=1.0)
async def _fetch_trace_proxy(url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID by proxying to SRE agent."""
    sre_agent_url = os.getenv("SRE_AGENT_URL", "http://sre-agent:8080")
    url = f"{sre_agent_url}/trace/{trace_id}"
    params = {"project_id": project_id}
    try:
        return await _fetch_trace_proxy(url, params)
    except Exception as e:
        logger.error(f"Failed to proxy trace lookup for {trace_id} after retries: {e}")
        if isinstance(e, httpx.HTTPStatusError):
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text) from e
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/trace")
async def get_trace_query(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID via query parameters by proxying to SRE agent."""
    return await get_trace(trace_id, project_id)


@router.get("/chat", response_class=HTMLResponse)
async def get_chat_ui() -> HTMLResponse:
    """Serves the rich SRE Chat interface Web page."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    try:
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"Failed to load chat UI file: {e}")
        raise HTTPException(status_code=500, detail=f"SRE Agent Chat UI Load Failure: {e!s}") from e


@router.get("/playground", response_class=HTMLResponse, include_in_schema=False)
async def get_a2ui_playground() -> FileResponse:
    """A page to edit A2UI messages and see them rendered (workshop/basics/a2ui.md)."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "playground.html"), media_type="text/html")


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@router.get("/static/playground-examples.json", include_in_schema=False)
async def playground_examples() -> FileResponse:
    """The example surfaces of the A2UI playground. A test validates them with a2ui-core."""
    return FileResponse(os.path.join(STATIC_DIR, "playground-examples.json"), media_type="application/json")


@router.get("/static/sre-a2ui.js", include_in_schema=False)
async def a2ui_renderer() -> FileResponse:
    """The chat UI's A2UI renderer bundle (built from agent/web).

    no-cache: browsers revalidate it on every load (a cheap 304 when unchanged), so a
    deploy reaches them at once instead of after a stale cached copy expires.
    """
    return FileResponse(
        os.path.join(STATIC_DIR, "sre-a2ui.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"}
    )


@router.post("/chat")
@otel_trace("routes.chat")
async def chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Stream a chat turn through the policy-gated Orchestrator agent.

    Every prompt goes to the Antigravity agent. Diagnostics happen only when the
    agent calls `diagnose_sre`, the single tool its deny-by-default policy allows.
    """
    logger.info(f"Received chat request (conversation_id={request.conversation_id}): {request.prompt}")
    return await _stream_orchestrator_chat(request, fastapi_request)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


# Running turns, by conversation. A turn runs as its own task, not inside the HTTP
# stream: a phone suspends the browser's connection when the user switches apps, and
# the answer must still be saved. POST /sessions/{id}/cancel stops a turn on purpose.
_running_turns: dict[str, asyncio.Task] = {}


@router.post("/sessions/{conversation_id}/cancel")
async def cancel_turn(conversation_id: str) -> dict[str, Any]:
    """Stops the running turn of a conversation (the chat's Stop button)."""
    task = _running_turns.get(conversation_id)
    if task is None or task.done():
        return {"status": "idle", "conversation_id": conversation_id}
    task.cancel()
    return {"status": "cancelled", "conversation_id": conversation_id}


async def _run_turn(request: ChatRequest, turn: _Turn, emit: Callable[[dict[str, Any]], None]) -> None:
    """Runs one turn of the Orchestrator agent, reports it through `emit`, and saves its answer."""
    reply, thinking, tool_calls = "", [], []

    def think(text: str) -> None:
        thinking.append(text)
        emit({"type": "thought", "text": text})

    # Set in this task's own context, so the tools and the tasks the SDK spawns see it.
    sink = DiagnosisSink(on_progress=think, context_id=turn.conv_id)
    diagnosis_sink.set(sink)
    response = None
    try:
        config = load_firestore_agent_config(conversation_id=turn.conv_id)
        config.prompt = request.prompt
        # No history replay here: the agent's memory of earlier turns is the Antigravity
        # harness state, which the Firestore strategy restores for a known conversation_id.
        async with Agent(config) as agent:
            response = await agent.chat(request.prompt)
            async for chunk in response.chunks:
                kind = chunk.__class__.__name__
                if kind == "Thought":
                    think(chunk.text)
                elif kind == "ToolCall":
                    tool_calls.append(chunk.name)
                    emit({"type": "thought", "text": f"🔧 Calling tool `{chunk.name}`..."})
                elif kind == "Text":
                    reply += chunk.text
                    emit({"type": "chunk", "text": chunk.text})

        # The model replies with a short summary; under it the UI renders the SRE skill's
        # result - its A2UI surface, or its Markdown without one.
        rendered = sink.report if sink.report and not sink.report.startswith("Error:") else ""
        _, model = _turn_entries(request.prompt, reply, thinking, tool_calls, rendered, sink.a2ui)
        await _finish_turn(turn, model)
        emit(
            {
                "type": "done",
                "conversation_id": turn.conv_id,
                "response": reply,
                "a2ui": model.get("a2ui", []),
                "rendered": model.get("rendered", ""),
            }
        )
    except asyncio.CancelledError:
        logger.info(f"Turn of conversation {turn.conv_id} stopped by the user.")
        if response is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(response.cancel())
        with contextlib.suppress(Exception):
            await asyncio.shield(_finish_turn(turn, {"source": "MODEL", "content": "Stopped.", "stopped": True}))
        raise
    except Exception as e:
        logger.exception(f"Turn of conversation {turn.conv_id} failed.")
        detail = str(e) or e.__class__.__name__
        with contextlib.suppress(Exception):
            await _finish_turn(turn, {"source": "MODEL", "content": f"Error: {detail}", "error": True})
        emit({"type": "error", "detail": detail})


async def _stream_orchestrator_chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Starts a turn of the Orchestrator agent and streams it as SSE.

    The turn runs as its own task: when the client goes away (e.g. a phone switched
    apps), the stream ends but the turn finishes and saves its answer, which the
    chat loads from the history when it is back.
    """

    async def event_generator():
        try:
            # A new chat gets its ID now, before the agent runs: the browser registers it
            # (sidebar, URL) from the first event, and it survives a failed or interrupted
            # turn. The agent creates the conversation under this ID and resumes it later.
            conv_id = _resolve_conversation_id(request.conversation_id)
            running = _running_turns.get(conv_id)
            if running is not None and not running.done():
                yield _sse({"type": "error", "detail": "This chat is still working on the previous message."})
                return
            if conv_id != request.conversation_id:
                await _register_session(conv_id, request.prompt)
            user, _ = _turn_entries(request.prompt, "", [], [], "")
            turn = await _start_turn(conv_id, request.prompt, user)
        except Exception as e:
            logger.exception("Failed to start the chat turn.")
            yield _sse({"type": "error", "detail": str(e) or e.__class__.__name__})
            return

        # Start the turn before the first event: a client may leave right after it.
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = asyncio.create_task(_run_turn(request, turn, events.put_nowait))
        _running_turns[conv_id] = task
        task.add_done_callback(lambda _: _running_turns.pop(conv_id, None))
        yield _sse({"type": "start", "conversation_id": conv_id, "prompt": request.prompt})

        while True:
            try:
                # Wake up regularly to notice a client that went away during a long tool call.
                event = await asyncio.wait_for(events.get(), timeout=1.0)
            except TimeoutError:
                if task.done() and events.empty():
                    return  # stopped: the client cancelled the turn
                if await fastapi_request.is_disconnected():
                    logger.info(f"Client left conversation {conv_id}; its turn keeps running.")
                    return
                continue
            yield _sse(event)
            if event["type"] in ("done", "error"):
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
