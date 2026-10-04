"""REST routes of the SRE Diagnostics service: health and trace lookups.

The agent itself is served over A2A (see `sre_agent.a2a_agent`).
"""

import json
import logging

from fastapi import APIRouter, HTTPException

from sre_common import otel_trace

logger = logging.getLogger("sre_agent.routes")

router = APIRouter()


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
