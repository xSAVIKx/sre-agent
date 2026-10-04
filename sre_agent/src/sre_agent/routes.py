"""API Route definitions for the SRE Diagnostics Agent."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sre_agent.diagnosis import Report, run_diagnosis
from sre_common import otel_trace

logger = logging.getLogger("sre_agent.routes")

router = APIRouter()


class SreMessageRequest(BaseModel):
    """Pydantic model representing an A2A message request to the SRE Agent."""

    prompt: str
    conversation_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


@router.get("/health")
@otel_trace("sre_agent.health_check")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "healthy"}


@router.get("/trace/{trace_id}")
@otel_trace("sre_agent.get_trace")
async def get_trace(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID."""
    logger.info(f"Retrieving trace details via GET for trace_id={trace_id}")
    try:
        from sre_agent.gcp_tools import get_trace_details

        details_str = await get_trace_details(trace_id, project_id)
        return json.loads(details_str)
    except Exception as e:
        logger.error(f"Failed to get trace details for {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve trace: {e!s}") from e


@router.get("/trace")
@otel_trace("sre_agent.get_trace_query")
async def get_trace_query(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID via query parameters."""
    logger.info(f"Retrieving trace details via GET query for trace_id={trace_id}")
    try:
        from sre_agent.gcp_tools import get_trace_details

        details_str = await get_trace_details(trace_id, project_id)
        return json.loads(details_str)
    except Exception as e:
        logger.error(f"Failed to get trace details for {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve trace: {e!s}") from e


@router.post("/v1/agents/sre/messages", deprecated=True)
@otel_trace("sre_agent.sre_message")
async def sre_message(request: SreMessageRequest, fastapi_request: Request):
    """Legacy SSE endpoint, kept until the Orchestrator speaks A2A. Use the A2A agent at "/"."""
    logger.info(f"Received legacy SRE request for project={request.project_id}")

    async def event_generator():
        try:
            async for update in run_diagnosis(
                prompt=request.prompt,
                project_id=request.project_id,
                refresh=request.refresh,
                conversation_id=request.conversation_id,
            ):
                if await fastapi_request.is_disconnected():
                    return
                if isinstance(update, Report):
                    yield f"data: {json.dumps({'type': 'done', 'response': update.text})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'thought', 'text': update.text})}\n\n"
        except Exception as e:
            logger.exception("Failed inside SRE Agent messages stream.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
