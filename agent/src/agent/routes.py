"""FastAPI Route Definitions for Orchestrator Agent.

Exposes endpoints for health check, session management, trace proxy, and stateful A2A chat orchestration.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent.a2ui_translator import translate_markdown_to_a2ui
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


class ChatRequest(BaseModel):
    """Pydantic model representing a stateful chat request."""

    prompt: str
    conversation_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


class ChatResponse(BaseModel):
    """Pydantic model representing a stateful chat response with A2UI."""

    status: str
    response: str
    response_a2ui: dict[str, Any] | None = None
    conversation_id: str | None = None


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
    prompt: str, reply: str, thinking: list[str], tool_calls: list[str], rendered: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The compact user/model pair one chat turn adds to the transcript.

    Only what the UI replays is kept. The raw SDK steps are far too large to store
    (one diagnosis is ~90 streamed steps), and the A2UI payload is rebuilt on read.
    `rendered` is the Markdown the UI rendered when it differs from the reply (the
    full sub-agent report when the model only summarized it).
    """
    user = {"source": "USER", "content": prompt}
    model: dict[str, Any] = {"source": "MODEL", "content": reply}
    if thinking:
        model["thinking"] = "\n".join(thinking)
    if tool_calls:
        model["tool_calls"] = [{"name": name} for name in tool_calls]
    if rendered and rendered != reply:
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


async def _save_turn(conv_id: str, prompt: str, user: dict[str, Any], model: dict[str, Any]) -> None:
    """Appends one turn to the conversation's transcript.

    Each turn is its own document under agent_sessions/{id}/turns, so a long
    conversation never approaches Firestore's 1 MiB document limit - the session
    document itself also holds the Antigravity harness snapshot.
    """
    if not HAS_ANTIGRAVITY:
        from agent.config import MOCK_HISTORY_DB

        MOCK_HISTORY_DB.setdefault(conv_id, []).extend([user, model])
        return

    from google.cloud import firestore

    db = firestore.AsyncClient()
    session = db.collection(SESSIONS_COLLECTION).document(conv_id)
    await session.collection(TURNS_COLLECTION).add(
        {"created_at": firestore.SERVER_TIMESTAMP, "user": user, "model": model}
    )
    existing = await session.get()
    update: dict[str, Any] = {"conversation_id": conv_id, "updated_at": firestore.SERVER_TIMESTAMP}
    if not (existing.exists and (existing.to_dict() or {}).get("prompt")):
        update["prompt"] = prompt
    await session.set(update, merge=True)


def _with_a2ui(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of the entries, with the A2UI payload rebuilt for model entries lacking one."""
    result = []
    for entry in entries:
        entry = dict(entry)
        if entry.get("source") == "MODEL" and not entry.get("response_a2ui"):
            markdown = entry.get("rendered") or entry.get("content") or ""
            if markdown:
                entry["response_a2ui"] = translate_markdown_to_a2ui(markdown)
        result.append(entry)
    return result


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
            history.extend(entry for entry in (data.get("user"), data.get("model")) if entry)
        if not history:
            # Sessions saved before transcripts moved to a subcollection keep them inline.
            doc = await session.get()
            history = (doc.to_dict() or {}).get("history", []) if doc.exists else []
        return {"conversation_id": conversation_id, "history": _with_a2ui(history)}
    except Exception as e:
        logger.warning(f"Using mock database history fallback: {e}")
        from agent.config import MOCK_HISTORY_DB

        return {
            "conversation_id": conversation_id,
            "history": _with_a2ui(MOCK_HISTORY_DB.get(conversation_id, [])),
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


async def _stream_orchestrator_chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Invokes the Orchestrator agent reasoning loop and streams it as SSE."""

    async def event_generator():
        # Progress from diagnose_sre and chunks from the agent share one queue,
        # so sub-agent progress shows up while the tool call is still running.
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        sink = DiagnosisSink(on_progress=lambda text: queue.put_nowait(("progress", text)))
        # Set before the agent starts so tasks the SDK spawns inherit it.
        sink_token = diagnosis_sink.set(sink)
        response = None
        try:
            # A new chat gets its ID now, before the agent runs: the browser registers it
            # (sidebar, URL) from the first event, and it survives a failed or interrupted
            # turn. The agent creates the conversation under this ID and resumes it later.
            conv_id = _resolve_conversation_id(request.conversation_id)
            if conv_id != request.conversation_id:
                await _register_session(conv_id, request.prompt)
            yield _sse({"type": "start", "conversation_id": conv_id})
            # One A2A context per chat conversation, so the SRE agent keeps its session.
            sink.context_id = conv_id

            config = load_firestore_agent_config(conversation_id=conv_id)
            config.prompt = request.prompt

            async with Agent(config) as agent:
                # No history replay here: the agent's memory of earlier turns is the
                # Antigravity harness state, which the Firestore strategy restores for a
                # known conversation_id. The transcript below only feeds the UI.

                response = await agent.chat(request.prompt)

                async def pump_chunks() -> None:
                    try:
                        async for chunk in response.chunks:
                            await queue.put(("chunk", chunk))
                    except Exception as exc:
                        await queue.put(("error", exc))
                    finally:
                        await queue.put(("end", None))

                pump = asyncio.create_task(pump_chunks())
                accumulated_text = ""
                thinking: list[str] = []
                tool_calls: list[str] = []
                disconnected = False
                finished = False
                try:
                    while True:
                        try:
                            # Wake up regularly so a long, silent tool call still
                            # notices a client that went away.
                            kind, item = await asyncio.wait_for(queue.get(), timeout=1.0)
                        except TimeoutError:
                            if await fastapi_request.is_disconnected():
                                disconnected = True
                                break
                            continue
                        if kind == "end":
                            finished = True
                            break
                        if kind == "error":
                            raise item
                        if await fastapi_request.is_disconnected():
                            disconnected = True
                            break
                        if kind == "progress":
                            thinking.append(item)
                            yield _sse({"type": "thought", "text": item})
                            continue

                        cls_name = item.__class__.__name__
                        if cls_name == "Thought":
                            thinking.append(item.text)
                            yield _sse({"type": "thought", "text": item.text})
                        elif cls_name == "ToolCall":
                            tool_calls.append(item.name)
                            yield _sse({"type": "thought", "text": f"🔧 Calling tool `{item.name}`..."})
                        elif cls_name == "Text":
                            accumulated_text += item.text
                            yield _sse({"type": "chunk", "text": item.text})
                finally:
                    # Runs on normal exit, errors, and when Starlette cancels the
                    # stream because the client disconnected: stop the agent turn
                    # and the pump instead of leaving them running.
                    if not finished:
                        logger.info("Chat stream ended early (client gone or error). Cancelling the agent turn.")
                        with contextlib.suppress(Exception, asyncio.CancelledError):
                            await asyncio.shield(response.cancel())
                    if not pump.done():
                        pump.cancel()
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await pump

                # Stream complete
                if not disconnected and not await fastapi_request.is_disconnected():
                    # The model may summarize the tool output; render the full
                    # sub-agent report when the reply lost the post-mortem.
                    rendered = accumulated_text
                    if sink.report and "Incident Post-Mortem" in sink.report and "Incident Post-Mortem" not in rendered:
                        rendered = sink.report
                    response_a2ui = translate_markdown_to_a2ui(rendered)

                    user_entry, model_entry = _turn_entries(
                        request.prompt, accumulated_text, thinking, tool_calls, rendered
                    )
                    try:
                        await _save_turn(conv_id, request.prompt, user_entry, model_entry)
                    except Exception:
                        logger.exception(f"Failed to save the transcript of conversation {conv_id}")

                    yield _sse(
                        {
                            "type": "done",
                            "conversation_id": conv_id,
                            "response": accumulated_text,
                            "response_a2ui": response_a2ui,
                        }
                    )

        except Exception as e:
            logger.exception("Failed inside Orchestrator chat stream.")
            yield _sse({"type": "error", "detail": str(e) or e.__class__.__name__})
        finally:
            with contextlib.suppress(ValueError):  # reset from a different context
                diagnosis_sink.reset(sink_token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
