"""FastAPI Route Definitions for Orchestrator Agent.

Exposes endpoints forhealth check, session management, trace proxy, and stateful A2A chat orchestration.
"""

import os
import logging
from typing import Any
import json
import asyncio
from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import httpx
import datetime
from sre_common import retry_async

from agent.config import (
    load_agent_config,
    load_firestore_agent_config,
    Agent,
    HAS_ANTIGRAVITY,
    Text,
    Thought,
    ToolCall,
    ToolResult,
)
from agent.a2ui_translator import translate_markdown_to_a2ui

logger = logging.getLogger("orchestrator_agent.routes")

router = APIRouter()


# Define simple otel_trace fallback decorator locally
def otel_trace(span_name: str):
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


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
    """Trigger SRE diagnostics (delegates to SRE agent via A2A)."""
    logger.info(f"Received SRE diagnostics request: {request.prompt}")
    sre_agent_url = os.getenv("SRE_AGENT_URL", "http://sre-agent:8080")
    url = f"{sre_agent_url}/v1/agents/sre/messages"
    payload = {
        "prompt": request.prompt,
        "project_id": request.project_id
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=300.0)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"SRE Sub-Agent Error: {response.text}")
            
            result = ""
            for line in response.iter_lines():
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        if event.get("type") == "done":
                            result = event.get("response", "")
                            break
                    except Exception:
                        pass
                        
            return DiagnoseResponse(status="success", result=result)
    except Exception as e:
        logger.exception("Failed SRE diagnostics proxy.")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def get_sessions():
    """Retrieve all available diagnostic sessions with metadata."""
    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        collection = db.collection("agent_sessions")
        docs = await collection.select(["conversation_id", "updated_at", "prompt"]).order_by("updated_at", direction=firestore.Query.DESCENDING).limit(50).get()
        sessions = []
        for doc in docs:
            data = doc.to_dict()
            updated_at = data.get("updated_at")
            updated_at_str = updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)
            sessions.append({
                "conversation_id": doc.id,
                "prompt": data.get("prompt") or "Untitled Session",
                "updated_at": updated_at_str
            })
        return sessions
    except Exception as e:
        logger.warning(f"Using mock database retrieval for sessions: {e}")
        from agent.config import MOCK_HISTORY_DB
        sessions = []
        for conv_id, steps in MOCK_HISTORY_DB.items():
            sessions.append({
                "conversation_id": conv_id,
                "prompt": steps[0]["content"] if steps else "Untitled Session",
                "updated_at": datetime.datetime.now().isoformat()
            })
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
        raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/sessions/{conversation_id}")
async def delete_session(conversation_id: str):
    """Delete an existing session."""
    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        doc_ref = db.collection("agent_sessions").document(conversation_id)
        await doc_ref.delete()
        return {"status": "success", "conversation_id": conversation_id}
    except Exception as e:
        logger.warning(f"Using mock database delete fallback: {e}")
        from agent.config import MOCK_HISTORY_DB
        if conversation_id in MOCK_HISTORY_DB:
            del MOCK_HISTORY_DB[conversation_id]
        return {"status": "success", "conversation_id": conversation_id}


@router.get("/sessions/{conversation_id}/history")
async def get_session_history(conversation_id: str):
    """Retrieve the conversation step history for a specific session."""
    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        doc = await db.collection("agent_sessions").document(conversation_id).get()
        if doc.exists:
            return {"conversation_id": conversation_id, "history": doc.to_dict().get("history", [])}
        return {"conversation_id": conversation_id, "history": []}
    except Exception as e:
        logger.warning(f"Using mock database history fallback: {e}")
        from agent.config import MOCK_HISTORY_DB
        return {"conversation_id": conversation_id, "history": MOCK_HISTORY_DB.get(conversation_id, [])}


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
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trace")
async def get_trace_query(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID via query parameters by proxying to SRE agent."""
    return await get_trace(trace_id, project_id)


@router.get("/chat", response_class=HTMLResponse)
async def get_chat_ui() -> HTMLResponse:
    """Serves the rich SRE Chat interface Web page."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"Failed to load chat UI file: {e}")
        raise HTTPException(status_code=500, detail=f"SRE Agent Chat UI Load Failure: {str(e)}")


@router.post("/chat")
@otel_trace("routes.chat")
async def chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Trigger stateful A2A chat routing or general orchestrator chat."""
    logger.info(f"Received chat request (conversation_id={request.conversation_id}): {request.prompt}")

    # 1. Infer if the request is an SRE diagnostics command
    # SRE-related keywords trigger direct A2A SRE streaming proxy
    is_sre_prompt = any(x in request.prompt.lower() for x in ("diagnose", "latency", "error", "trace", "sre", "monkey", "scan"))
    is_refresh = request.refresh or any(x in request.prompt.lower() for x in ("rescan", "refresh", "re-discover", "re-scan"))

    # 2. Check if this conversation was already an SRE session
    is_sre_session = False
    conv_id = request.conversation_id
    if conv_id:
        try:
            from google.cloud import firestore
            db = firestore.AsyncClient()
            doc = await db.collection("agent_sessions").document(conv_id).get()
            if doc.exists:
                # If there are tool calls to diagnose_sre, it is an SRE session
                history = doc.to_dict().get("history", [])
                for step in history:
                    for tc in step.get("tool_calls", []):
                        if tc.get("name") == "diagnose_sre":
                            is_sre_session = True
                            break
        except Exception:
            pass

    if is_sre_prompt or is_sre_session or is_refresh:
        logger.info("Routing request to A2A SRE Sub-Agent...")
        return await _stream_sre_agent_a2a(request, fastapi_request, is_refresh)

    # 3. Fallback to normal Orchestrator LLM chat for general conversation
    return await _stream_orchestrator_chat(request, fastapi_request)


async def _stream_sre_agent_a2a(request: ChatRequest, fastapi_request: Request, is_refresh: bool) -> StreamingResponse:
    """Invokes SRE sub-agent directly via A2A HTTP/SSE and forwards stream to the browser."""
    sre_agent_url = os.getenv("SRE_AGENT_URL", "http://sre-agent:8080")
    url = f"{sre_agent_url}/v1/agents/sre/messages"
    
    # Resolve or create conversation ID
    conv_id = request.conversation_id
    if not conv_id:
        import uuid
        conv_id = f"sre-{uuid.uuid4().hex}"

    payload = {
        "prompt": request.prompt,
        "conversation_id": conv_id,
        "project_id": request.project_id,
        "refresh": is_refresh
    }

    async def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': conv_id})}\n\n"
        
        accumulated_text = ""
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", url, json=payload, timeout=300.0) as response:
                    if response.status_code != 200:
                        yield f"data: {json.dumps({'type': 'error', 'detail': f'SRE sub-agent returned status {response.status_code}'})}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if await fastapi_request.is_disconnected():
                            logger.info("Client disconnected. Aborting SRE A2A stream.")
                            break
                        
                        if line.startswith("data: "):
                            try:
                                event_data = json.loads(line[6:])
                                ev_type = event_data.get("type")
                                if ev_type == "chunk":
                                    accumulated_text += event_data.get("text", "")
                                    yield f"data: {line[6:]}\n\n"
                                elif ev_type == "thought":
                                    yield f"data: {line[6:]}\n\n"
                                elif ev_type == "error":
                                    yield f"data: {line[6:]}\n\n"
                                    return
                            except Exception:
                                pass

            # Translate Markdown to A2UI component payload
            response_a2ui = translate_markdown_to_a2ui(accumulated_text)

            # Persist Orchestrator user session in Firestore
            user_step = {
                "step_index": 0,
                "type": "TEXT",
                "source": "USER",
                "target": "MODEL",
                "status": "SUCCESS",
                "content": request.prompt
            }
            
            model_step = {
                "step_index": 1,
                "type": "TEXT_RESPONSE",
                "source": "MODEL",
                "target": "TARGET_USER",
                "status": "DONE",
                "content": accumulated_text,
                "thinking": "Delegated SRE diagnostics to sub-agent.",
                "response_a2ui": response_a2ui,
                "tool_calls": [{"name": "diagnose_sre", "args": {"prompt": request.prompt}}]
            }

            history = [user_step, model_step]
            
            # Save history
            try:
                from google.cloud import firestore
                db = firestore.AsyncClient()
                doc_ref = db.collection("agent_sessions").document(conv_id)
                doc = await doc_ref.get()
                existing_prompt = doc.to_dict().get("prompt") if doc.exists else None
                
                update_data = {
                    "history": history,
                    "updated_at": firestore.SERVER_TIMESTAMP
                }
                if not existing_prompt or existing_prompt == "Untitled Session":
                    update_data["prompt"] = request.prompt
                
                await doc_ref.set(update_data, merge=True)
            except Exception as e:
                logger.warning(f"Using mock session persistence fallback: {e}")
                from agent.config import MOCK_HISTORY_DB
                MOCK_HISTORY_DB[conv_id] = history

            yield f"data: {json.dumps({'type': 'done', 'response': accumulated_text, 'response_a2ui': response_a2ui})}\n\n"

        except Exception as e:
            logger.exception("Error in SRE streaming proxy.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


async def _stream_orchestrator_chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Invokes local Orchestrator agent reasoning loop (standard conversation)."""
    async def event_generator():
        response = None
        try:
            config = load_firestore_agent_config(conversation_id=request.conversation_id)
            config.prompt = request.prompt

            async with Agent(config) as agent:
                conv_id = agent.conversation_id or request.conversation_id
                
                # Load history steps if available
                if conv_id:
                    if HAS_ANTIGRAVITY:
                        try:
                            from google.cloud import firestore
                            db = firestore.AsyncClient()
                            doc = await db.collection("agent_sessions").document(conv_id).get()
                            if doc.exists:
                                history_data = doc.to_dict().get("history", [])
                                from google.antigravity.types import Step
                                agent.conversation._steps = [Step(**step) for step in history_data]
                        except Exception:
                            pass
                    else:
                        from agent.config import MOCK_HISTORY_DB
                        from agent.config import MockStep
                        history_data = MOCK_HISTORY_DB.get(conv_id, [])
                        agent.conversation._steps = [MockStep(**step) for step in history_data]

                response = await agent.chat(request.prompt)
                
                yield f"data: {json.dumps({'type': 'start', 'conversation_id': conv_id})}\n\n"
                
                accumulated_text = ""
                async for chunk in response.chunks:
                    if await fastapi_request.is_disconnected():
                        logger.info("Client disconnected. Aborting orchestrator chat stream.")
                        await response.cancel()
                        break
                    
                    cls_name = chunk.__class__.__name__
                    if cls_name == "Thought":
                        yield f"data: {json.dumps({'type': 'thought', 'text': chunk.text})}\n\n"
                    elif cls_name == "Text":
                        accumulated_text += chunk.text
                        yield f"data: {json.dumps({'type': 'chunk', 'text': chunk.text})}\n\n"

                # Stream complete
                if not await fastapi_request.is_disconnected():
                    response_a2ui = translate_markdown_to_a2ui(accumulated_text)
                    
                    steps = []
                    for step in agent.conversation.history:
                        step_dict = step.model_dump(mode="json") if hasattr(step, "model_dump") else step.model_dump()
                        if step.source == "MODEL":
                            step_dict["response_a2ui"] = response_a2ui
                        steps.append(step_dict)

                    if HAS_ANTIGRAVITY:
                        try:
                            from google.cloud import firestore
                            db = firestore.AsyncClient()
                            doc_ref = db.collection("agent_sessions").document(conv_id)
                            await doc_ref.set({
                                "history": steps,
                                "updated_at": firestore.SERVER_TIMESTAMP,
                                "prompt": request.prompt
                            }, merge=True)
                        except Exception:
                            pass
                    else:
                        from agent.config import MOCK_HISTORY_DB
                        MOCK_HISTORY_DB[conv_id] = steps

                    yield f"data: {json.dumps({'type': 'done', 'response': accumulated_text, 'response_a2ui': response_a2ui})}\n\n"

        except Exception as e:
            logger.exception("Failed inside Orchestrator chat stream.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
