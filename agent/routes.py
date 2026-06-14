"""FastAPI Route Definitions for SRE Agent.

This module defines the HTTP endpoints (health, diagnose, chat UI, chat API)
for the standalone SRE agent service using a modular APIRouter setup.
"""

import os
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from agent.config import (
    load_agent_config,
    load_firestore_agent_config,
    Agent,
    HAS_ANTIGRAVITY,
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
async def chat(request: ChatRequest) -> ChatResponse:
    """Trigger a stateful agent chat request.

    Starts a new agent conversation or resumes an existing one from remote
    state persisted in Google Cloud Firestore.
    """
    logger.info(f"Received SRE chat request (conversation_id={request.conversation_id}): {request.prompt}")

    # Set project ID in environment if provided to affect the tools
    if request.project_id:
        os.environ["GCP_PROJECT"] = request.project_id

    try:
        config = load_firestore_agent_config(conversation_id=request.conversation_id)

        async with Agent(config) as agent:
            response = await agent.chat(request.prompt)
            result = await response.text()
            conv_id = agent.conversation_id

        # Translate markdown response into structured A2UI declarative JSON
        response_a2ui = translate_markdown_to_a2ui(result)

        logger.info(f"Successfully processed chat request. Active conversation ID: {conv_id}")
        return ChatResponse(
            status="success",
            response=result,
            response_a2ui=response_a2ui,
            conversation_id=conv_id,
        )

    except Exception as e:
        logger.exception("Failed to execute SRE agent chat.")
        raise HTTPException(
            status_code=500,
            detail=f"SRE Agent Chat Execution Failure: {str(e)}"
        )
