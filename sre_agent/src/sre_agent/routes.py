"""API Route definitions for the SRE Diagnostics Agent.
"""

import os
import json
import logging
import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import datetime

from sre_agent.config import IS_MOCK, INVENTORY_AGENT_URL, PROJECT_ID
from sre_agent.gcp_tools import query_traces
from sre_agent.sre_workflow import run_sre_diagnostics
from sre_agent.firestore_strategy import get_sre_session, save_sre_session
from sre_common.middleware import target_project_contextvar

logger = logging.getLogger("sre_agent.routes")

router = APIRouter()


class SreMessageRequest(BaseModel):
    """Pydantic model representing an A2A message request to the SRE Agent."""
    prompt: str
    conversation_id: str | None = None
    project_id: str | None = None
    refresh: bool = False


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "healthy"}


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str, project_id: str | None = None):
    """Get detailed spans for a specific trace ID."""
    logger.info(f"Retrieving trace details via GET for trace_id={trace_id}")
    try:
        from sre_agent.gcp_tools import get_trace_details
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
        from sre_agent.gcp_tools import get_trace_details
        details_str = await get_trace_details(trace_id, project_id)
        return json.loads(details_str)
    except Exception as e:
        logger.error(f"Failed to get trace details for {trace_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve trace: {str(e)}")


@router.post("/v1/agents/sre/messages")
async def sre_message(request: SreMessageRequest, fastapi_request: Request):
    """A2A HTTP streaming endpoint for SRE diagnostics.

    Runs SRE trace-log correlation and streams thoughts and final reports via SSE.
    """
    logger.info(f"Received SRE diagnostics request for project={request.project_id} (conversation_id={request.conversation_id})")

    resolved_project = request.project_id or PROJECT_ID
    # Set target project ContextVar for this request execution task context
    target_project_contextvar.set(resolved_project)

    async def event_generator():
        try:
            # 1. Fetch project topology cache from Inventory Agent (A2A call)
            yield f"data: {json.dumps({'type': 'thought', 'text': f'🔧 Contacting Inventory Agent to fetch topology for project `{resolved_project}`...'})}\n\n"
            
            topology = {}
            try:
                # Use default timeout of 10s to keep it responsive
                inv_url = f"{INVENTORY_AGENT_URL}/v1/agents/inventory"
                params = {"project_id": resolved_project, "refresh": request.refresh}
                async with httpx.AsyncClient() as client:
                    resp = await client.get(inv_url, params=params, timeout=15.0)
                    if resp.status_code == 200:
                        topology = resp.json()
                        status = topology.get("status")
                        if status == "DISCOVERING":
                            yield f"data: {json.dumps({'type': 'thought', 'text': '⚠️ Target project infrastructure discovery in progress. Diagnostic run may use cached or incomplete topology data.'})}\n\n"
                        else:
                            svc_count = len(topology.get("discovered_resources", {}).get("services", []))
                            topo_msg = f"✅ Topology cached successfully. Resolved {svc_count} active compute services."
                            yield f"data: {json.dumps({'type': 'thought', 'text': topo_msg})}\n\n"
                    else:
                        logger.warning(f"Inventory Agent returned status code: {resp.status_code}")
            except Exception as e:
                logger.error(f"Failed to query Inventory Agent: {e}")
                yield f"data: {json.dumps({'type': 'thought', 'text': '⚠️ Inventory Agent query failed. Proceeding with default service topology parameters.'})}\n\n"

            # 2. Retrieve recent traces
            yield f"data: {json.dumps({'type': 'thought', 'text': f'🔍 Fetching recent traces from project `{resolved_project}`...'})}\n\n"
            
            try:
                traces_json = await query_traces(project_id=resolved_project, limit=10)
            except Exception as e:
                logger.error(f"Failed to query traces: {e}")
                yield f"data: {json.dumps({'type': 'error', 'detail': f'Trace API query failed: {str(e)}'})}\n\n"
                return

            # 3. Run SRE ADK Multi-Agent Diagnostics
            yield f"data: {json.dumps({'type': 'thought', 'text': '🧠 Running multi-agent ADK correlation workflow (TraceAnalyzer + LogCorrelator)...'})}\n\n"
            
            # Execute workflow
            report = await run_sre_diagnostics(traces_json=traces_json, project_id=resolved_project)
            
            # 4. Stream final report to Orchestrator chunk-by-chunk
            # Yield thoughts complete
            yield f"data: {json.dumps({'type': 'thought', 'text': '✅ Diagnostics complete. Generating Markdown report...'})}\n\n"
            
            # Stream the report text
            words = report.split(" ")
            accumulated_text = ""
            for i, word in enumerate(words):
                if await fastapi_request.is_disconnected():
                    logger.info("Orchestrator client disconnected. Aborting stream.")
                    return
                space = " " if i < len(words) - 1 else ""
                chunk = word + space
                accumulated_text += chunk
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
                await asyncio.sleep(0.01)

            # 5. Persist private SRE session history
            if request.conversation_id:
                history_record = {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "prompt": request.prompt,
                    "traces_analyzed": traces_json,
                    "topology": topology,
                    "report": accumulated_text
                }
                
                # Retrieve existing history
                sess = await get_sre_session(request.conversation_id) or {}
                history = sess.get("history", [])
                history.append(history_record)
                
                await save_sre_session(request.conversation_id, history)

            # Done event
            yield f"data: {json.dumps({'type': 'done', 'response': accumulated_text})}\n\n"

        except Exception as e:
            logger.exception("Failed inside SRE Agent messages stream.")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
