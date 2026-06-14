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
                        response.cancel()
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
                            logger.warning(f"Loop prevention triggered: SRE Agent has executed {tool_count} tools in a single turn. Stopping process.")
                            response.cancel()
                            yield f"data: {json.dumps({'type': 'thought', 'text': '⚠️ Loop prevention triggered: SRE Agent has executed too many tools in a single turn. Stopping process to prevent infinite loop.\n'})}\n\n"
                            yield f"data: {json.dumps({'type': 'error', 'detail': 'Tool execution limit exceeded to prevent infinite loops.'})}\n\n"
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
                    yield f"data: {json.dumps({'type': 'done', 'response': accumulated_text, 'response_a2ui': response_a2ui})}\n\n"

        except asyncio.CancelledError:
            logger.info("Connection cancelled by client. Terminating SRE Agent execution.")
            if response is not None:
                response.cancel()
            raise
        except Exception as e:
            logger.exception("Failed to execute SRE agent chat stream.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
