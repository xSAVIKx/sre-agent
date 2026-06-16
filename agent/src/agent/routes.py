"""FastAPI Route Definitions for SRE Agent.

This module defines the HTTP endpoints (health, diagnose, chat UI, chat API)
for the standalone SRE agent service using a modular APIRouter setup.
"""

import os
import logging
from typing import Any
import json
import asyncio
from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
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
from skills.sre_incident_solver.gcp_tools import otel_trace

logger = logging.getLogger("sre_agent.routes")

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
    """Returns a 204 No Content for favicon requests to prevent 404 errors."""
    return Response(status_code=204)


@router.post("/diagnose")
@otel_trace("routes.diagnose")
async def diagnose(request: DiagnoseRequest) -> DiagnoseResponse:
    """Trigger the SRE agent diagnostics workflow.

    Initializes the SRE agent configuration, runs the agentic troubleshooting
    reasoning loop with safety policy gating, and returns the SRE diagnosis.
    """
    logger.info(f"Received SRE diagnostics request: {request.prompt}")

    # Set project ID in environment if provided to affect the tools
    if request.project_id:
        os.environ["GCP_PROJECT"] = request.project_id

    try:
        config = load_agent_config()

        async with Agent(config) as agent:
            response = await agent.chat(request.prompt)
            result = await response.text()

        logger.info("Successfully generated SRE diagnostics report.")
        return DiagnoseResponse(status="success", result=result)

    except Exception as e:
        logger.exception("Failed to execute SRE diagnostics.")
        raise HTTPException(
            status_code=500,
            detail=f"SRE Agent Execution Failure: {str(e)}"
        )


@router.get("/sessions")
async def get_sessions():
    """Retrieve all available diagnostic sessions with metadata."""
    if not HAS_ANTIGRAVITY:
        from agent.firestore_strategy import MOCK_FIRESTORE_DB
        sessions = []
        for conv_id, data in MOCK_FIRESTORE_DB.items():
            updated_at = data.get("updated_at")
            updated_at_str = updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)
            sessions.append({
                "conversation_id": conv_id,
                "prompt": data.get("prompt") or "Untitled Session",
                "updated_at": updated_at_str
            })
        sessions.sort(key=lambda x: x["updated_at"], reverse=True)
        return sessions

    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        collection = db.collection("agent_sessions")
        # Fetch only metadata fields: conversation_id, updated_at, prompt
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
        logger.error(f"Failed to fetch Firestore sessions: {e}")
        return []


class RenameSessionRequest(BaseModel):
    title: str


@router.put("/sessions/{conversation_id}/title")
async def rename_session(conversation_id: str, request: RenameSessionRequest):
    """Rename/modify the title of an existing session."""
    logger.info(f"Renaming session {conversation_id} to: {request.title}")

    if not HAS_ANTIGRAVITY:
        from agent.firestore_strategy import MOCK_FIRESTORE_DB
        if conversation_id in MOCK_FIRESTORE_DB:
            MOCK_FIRESTORE_DB[conversation_id]["prompt"] = request.title
            return {"status": "success", "conversation_id": conversation_id, "title": request.title}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        doc_ref = db.collection("agent_sessions").document(conversation_id)
        await doc_ref.set({"prompt": request.title}, merge=True)
        return {"status": "success", "conversation_id": conversation_id, "title": request.title}
    except Exception as e:
        logger.error(f"Failed to rename Firestore session {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to rename session: {str(e)}")


@router.delete("/sessions/{conversation_id}")
async def delete_session(conversation_id: str):
    """Delete an existing session."""
    logger.info(f"Deleting session: {conversation_id}")

    if not HAS_ANTIGRAVITY:
        from agent.firestore_strategy import MOCK_FIRESTORE_DB
        if conversation_id in MOCK_FIRESTORE_DB:
            del MOCK_FIRESTORE_DB[conversation_id]
        return {"status": "success", "conversation_id": conversation_id}

    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        doc_ref = db.collection("agent_sessions").document(conversation_id)
        await doc_ref.delete()
        return {"status": "success", "conversation_id": conversation_id}
    except Exception as e:
        logger.error(f"Failed to delete Firestore session {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {str(e)}")


@router.get("/sessions/{conversation_id}/history")
async def get_session_history(conversation_id: str):
    """Retrieve the conversation step history for a specific session."""
    if not HAS_ANTIGRAVITY:
        from agent.firestore_strategy import MOCK_FIRESTORE_DB
        session = MOCK_FIRESTORE_DB.get(conversation_id, {})
        steps = session.get("history", [])
        return {
            "conversation_id": conversation_id,
            "history": steps
        }

    try:
        from google.cloud import firestore
        db = firestore.AsyncClient()
        doc_ref = db.collection("agent_sessions").document(conversation_id)
        doc = await doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            steps = data.get("history", [])
            return {
                "conversation_id": conversation_id,
                "history": steps
            }
        else:
            return {
                "conversation_id": conversation_id,
                "history": []
            }
    except Exception as e:
        logger.error(f"Failed to load session history for {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve session history: {str(e)}")


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID."""
    logger.info(f"Retrieving trace details via GET for trace_id={trace_id}")
    try:
        from skills.sre_incident_solver.gcp_tools import get_trace_details
        details_str = await get_trace_details(trace_id, project_id)
        return json.loads(details_str)
    except Exception as e:
        logger.error(f"Failed to get trace details for {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve trace: {str(e)}")


@router.get("/trace")
async def get_trace_query(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID via query parameters."""
    logger.info(f"Retrieving trace details via GET query for trace_id={trace_id}")
    try:
        from skills.sre_incident_solver.gcp_tools import get_trace_details
        details_str = await get_trace_details(trace_id, project_id)
        return json.loads(details_str)
    except Exception as e:
        logger.error(f"Failed to get trace details for {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve trace: {str(e)}")


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
        raise HTTPException(
            status_code=500,
            detail=f"SRE Agent Chat UI Load Failure: {str(e)}"
        )


@router.post("/chat")
@otel_trace("routes.chat")
async def chat(request: ChatRequest, fastapi_request: Request) -> StreamingResponse:
    """Trigger a stateful agent chat request.

    Starts a new agent conversation or resumes an existing one from remote
    state persisted in Google Cloud Firestore, streaming progress in real-time.
    """
    logger.info(f"Received SRE chat request (conversation_id={request.conversation_id}): {request.prompt}")

    # Set project ID in environment if provided to affect the tools
    if request.project_id:
        os.environ["GCP_PROJECT"] = request.project_id

    async def event_generator():
        response = None
        try:
            config = load_firestore_agent_config(conversation_id=request.conversation_id)
            config.prompt = request.prompt

            async with Agent(config) as agent:
                conv_id = agent.conversation_id or request.conversation_id

                # Load existing history steps and populate agent.conversation._steps
                if conv_id:
                    if not HAS_ANTIGRAVITY:
                        from agent.firestore_strategy import MOCK_FIRESTORE_DB
                        session = MOCK_FIRESTORE_DB.get(conv_id, {})
                        history_data = session.get("history", [])
                        from agent.config import MockStep
                        agent.conversation._steps = [MockStep(**step_dict) for step_dict in history_data]
                    else:
                        try:
                            from google.cloud import firestore
                            db = firestore.AsyncClient()
                            doc = await db.collection("agent_sessions").document(conv_id).get()
                            if doc.exists:
                                data = doc.to_dict()
                                history_data = data.get("history", [])
                                from google.antigravity.types import Step
                                agent.conversation._steps = []
                                for step_dict in history_data:
                                    try:
                                        agent.conversation._steps.append(Step(**step_dict))
                                    except Exception as ex:
                                        logger.error(f"Failed to deserialize history step: {ex}, step_dict: {step_dict}")
                        except Exception as e:
                            logger.error(f"Failed to load history from Firestore for context preservation: {e}")

                response = await agent.chat(request.prompt)
                
                conv_id = request.conversation_id
                # 1. Start event (if we already have a conversation_id from request)
                if conv_id:
                    yield f"data: {json.dumps({'type': 'start', 'conversation_id': conv_id})}\n\n"

                start_yielded = bool(conv_id)
                accumulated_text = ""
                tool_count = 0

                # 2. Stream chunks
                async for chunk in response.chunks:
                    if not start_yielded:
                        conv_id = agent.conversation_id
                        yield f"data: {json.dumps({'type': 'start', 'conversation_id': conv_id})}\n\n"
                        start_yielded = True
                    # Check if client has disconnected (e.g. user clicked Stop or closed window)
                    if await fastapi_request.is_disconnected():
                        logger.info("Client disconnected. Cancelling SRE Agent execution.")
                        await response.cancel()
                        break

                    cls_name = chunk.__class__.__name__

                    if cls_name == "Thought":
                        yield f"data: {json.dumps({'type': 'thought', 'text': chunk.text})}\n\n"
                    elif cls_name == "Text":
                        accumulated_text += chunk.text
                        yield f"data: {json.dumps({'type': 'chunk', 'text': chunk.text})}\n\n"
                    elif cls_name == "ToolCall":
                        tool_count += 1
                        if tool_count > 20:
                            logger.warning(f"Loop prevention triggered: SRE Agent has executed {tool_count} tools in a single turn. Suspending to ask user for guidance.")
                            await response.cancel()
                            response_a2ui = translate_markdown_to_a2ui(accumulated_text)
                            yield f"data: {json.dumps({'type': 'limit_reached', 'response': accumulated_text, 'response_a2ui': response_a2ui})}\n\n"
                            break

                        # Live feedback of tool execution start
                        tool_desc = f"🔧 Calling tool '{chunk.name}'"
                        if getattr(chunk, "args", None):
                            # Filter args for log output clean representation
                            tool_desc += f" with arguments {json.dumps(chunk.args)}"
                        tool_desc += "...\n"
                        yield f"data: {json.dumps({'type': 'thought', 'text': tool_desc})}\n\n"
                    elif cls_name == "ToolResult":
                        # Live feedback of tool execution complete
                        res_desc = f"✅ Tool '{chunk.name}' completed.\n"
                        yield f"data: {json.dumps({'type': 'thought', 'text': res_desc})}\n\n"

                # Check connection again before translating and finishing
                if not await fastapi_request.is_disconnected() and tool_count <= 20:
                    # 3. Translate markdown response into structured A2UI declarative JSON
                    response_a2ui = translate_markdown_to_a2ui(accumulated_text)

                    logger.info(f"Successfully processed chat stream. Active conversation ID: {conv_id}")

                    # Capture updated history steps
                    steps = []
                    for step in agent.conversation.history:
                        step_dict = step.model_dump(mode="json")
                        # Add response_a2ui to the final model response step if applicable
                        if step.source == "MODEL" and getattr(step, "is_complete_response", True):
                            step_dict["response_a2ui"] = response_a2ui
                        steps.append(step_dict)

                    if not HAS_ANTIGRAVITY:
                        from agent.firestore_strategy import MOCK_FIRESTORE_DB
                        if conv_id not in MOCK_FIRESTORE_DB:
                            MOCK_FIRESTORE_DB[conv_id] = {}
                        MOCK_FIRESTORE_DB[conv_id]["history"] = steps
                        if not MOCK_FIRESTORE_DB[conv_id].get("prompt") or MOCK_FIRESTORE_DB[conv_id]["prompt"] == "Untitled Session":
                            MOCK_FIRESTORE_DB[conv_id]["prompt"] = request.prompt
                    else:
                        try:
                            from google.cloud import firestore
                            db = firestore.AsyncClient()
                            doc_ref = db.collection("agent_sessions").document(conv_id)
                            doc = await doc_ref.get()
                            current_prompt = None
                            if doc.exists:
                                current_prompt = doc.to_dict().get("prompt")
                            
                            update_data = {
                                "history": steps,
                                "updated_at": firestore.SERVER_TIMESTAMP
                            }
                            if not current_prompt or current_prompt == "Untitled Session":
                                update_data["prompt"] = request.prompt
                            
                            await doc_ref.set(update_data, merge=True)
                        except Exception as e:
                            logger.error(f"Failed to save history/prompt to Firestore: {e}")

                    yield f"data: {json.dumps({'type': 'done', 'response': accumulated_text, 'response_a2ui': response_a2ui})}\n\n"

        except asyncio.CancelledError:
            logger.info("Connection cancelled by client. Terminating SRE Agent execution.")
            if response is not None:
                await response.cancel()
            raise
        except Exception as e:
            logger.exception("Failed to execute SRE agent chat stream.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
