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


@router.get("/sessions/{conversation_id}/history")
async def get_session_history(conversation_id: str):
    """Retrieve the conversation step history for a specific session."""
    if not HAS_ANTIGRAVITY:
        from agent.config import MOCK_HISTORY_DB
        history_data = MOCK_HISTORY_DB.get(conversation_id, [])
        steps = []
        for i, msg in enumerate(history_data):
            steps.append({
                "step_index": i,
                "type": "TEXT",
                "source": "USER" if msg["role"] == "user" else "MODEL",
                "target": "MODEL" if msg["role"] == "user" else "USER",
                "content": msg["content"],
                "status": "SUCCESS"
            })
        return {
            "conversation_id": conversation_id,
            "history": steps
        }

    try:
        config = load_firestore_agent_config(conversation_id=conversation_id)
        async with Agent(config) as agent:
            conv = agent.conversation
            history_steps = []
            for step in conv.history:
                step_dict = step.model_dump(mode="json")
                history_steps.append(step_dict)
            return {
                "conversation_id": conversation_id,
                "history": history_steps
            }
    except Exception as e:
        logger.error(f"Failed to load session history for {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve session history: {str(e)}")


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
                response = await agent.chat(request.prompt)
                conv_id = agent.conversation_id

                # 1. Start event
                yield f"data: {json.dumps({'type': 'start', 'conversation_id': conv_id})}\n\n"

                accumulated_text = ""
                tool_count = 0

                # 2. Stream chunks
                async for chunk in response.chunks:
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
                        if tool_count > 6:
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
                if not await fastapi_request.is_disconnected() and tool_count <= 6:
                    # 3. Translate markdown response into structured A2UI declarative JSON
                    response_a2ui = translate_markdown_to_a2ui(accumulated_text)

                    logger.info(f"Successfully processed chat stream. Active conversation ID: {conv_id}")
                    if not HAS_ANTIGRAVITY:
                        from agent.config import MOCK_HISTORY_DB
                        if conv_id not in MOCK_HISTORY_DB:
                            MOCK_HISTORY_DB[conv_id] = []
                        MOCK_HISTORY_DB[conv_id].append({
                            "role": "user",
                            "content": request.prompt
                        })
                        MOCK_HISTORY_DB[conv_id].append({
                            "role": "model",
                            "content": accumulated_text
                        })
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
