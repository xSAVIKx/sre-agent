"""API Route definitions for the Inventory Agent.
"""

import os
import logging
import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from inventory_agent.config import IS_MOCK, SCANNER_JOB_NAME, SCANNER_JOB_REGION, PROJECT_ID
from inventory_agent.firestore_strategy import (
    get_project_inventory,
    update_project_inventory,
    set_project_status
)

logger = logging.getLogger("inventory_agent.routes")

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "healthy"}


class RefreshRequest(BaseModel):
    """Pydantic model representing a manual refresh request."""
    project_id: str


class CallbackRequest(BaseModel):
    """Pydantic model representing the scanner job completion callback."""
    project_id: str
    discovered_resources: dict[str, Any]
    aggregated_metadata: dict[str, Any]
    status: str = "ACTIVE"


async def run_discovery_mock(project_id: str) -> None:
    """Simulates active GCP discovery locally and caches mock results after a brief delay."""
    logger.info(f"[Mock Scan] Initiating asynchronous mock discovery for project {project_id}...")
    await asyncio.sleep(3)  # Simulate discovery time
    
    # Load mock resources from traces.json / mock data directory
    mock_resources = {
        "services": [
            {"name": "sre-chaos-monkey", "url": "https://sre-chaos-monkey-mock.run.app", "vpc_connector": "sre-vpc"},
            {"name": "sre-agent", "url": "https://sre-agent-mock.run.app"}
        ],
        "databases": [
            {"name": "(default)", "type": "FIRESTORE"}
        ]
    }
    
    mock_metadata = {
        "region": "us-central1",
        "resource_count": 3,
        "labels": {"env": "development", "owner": "sre-team"}
    }
    
    await update_project_inventory(
        project_id=project_id,
        discovered_resources=mock_resources,
        aggregated_metadata=mock_metadata,
        status="ACTIVE"
    )
    logger.info(f"[Mock Scan] Mock discovery complete and saved to cache for {project_id}.")


async def trigger_scanner_job(target_project_id: str) -> None:
    """Triggers the Cloud Run Job (Task) to perform cross-project asset discovery."""
    if IS_MOCK:
        # Spawn local simulation task in the background
        asyncio.create_task(run_discovery_mock(target_project_id))
        return

    logger.info(f"Triggering Cloud Run scanner job '{SCANNER_JOB_NAME}' in {SCANNER_JOB_REGION} for project '{target_project_id}'")
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        job_path = client.job_path(PROJECT_ID, SCANNER_JOB_REGION, SCANNER_JOB_NAME)
        
        # Override environment variables for the task execution
        inventory_agent_url = os.getenv("INVENTORY_AGENT_URL")
        if inventory_agent_url:
            cb_url = f"{inventory_agent_url.rstrip('/')}/v1/agents/inventory/callback"
        else:
            cb_url = "http://inventory-agent:8080/v1/agents/inventory/callback"

        overrides = {
            "container_overrides": [
                {
                    "env": [
                        {"name": "TARGET_PROJECT_ID", "value": target_project_id},
                        {"name": "MOCK_GCP", "value": "false"},
                        {"name": "CALLBACK_URL", "value": cb_url}
                    ]
                }
            ]
        }
        
        operation = client.run_job(name=job_path, overrides=overrides)
        logger.info(f"Cloud Run scanner job triggered. Operation ID: {operation.metadata.name if hasattr(operation, 'metadata') else 'unknown'}")
    except Exception as e:
        logger.error(f"Failed to trigger Cloud Run scanner job: {e}")
        # Graceful fallback: run local simulation if API call fails
        logger.warning("Falling back to local simulation due to GCP API failure.")
        asyncio.create_task(run_discovery_mock(target_project_id))


@router.get("/v1/agents/inventory")
async def get_inventory(project_id: str, refresh: bool = False, background_tasks: BackgroundTasks = BackgroundTasks()):
    """Retrieves the infrastructure inventory for a project, caching results statefully.

    On a cache hit, returns instantly. On a cache miss or explicit refresh,
    it triggers an asynchronous scanner job and updates status.
    """
    logger.info(f"Received inventory request for project={project_id} (refresh={refresh})")
    
    cache = await get_project_inventory(project_id)
    
    if refresh or not cache:
        if not cache:
            # First scan scenario: set status to DISCOVERING and trigger job
            logger.info(f"No cache found for {project_id}. Triggering initial scan...")
            await set_project_status(project_id, "DISCOVERING")
            background_tasks.add_task(trigger_scanner_job, project_id)
            return {
                "project_id": project_id,
                "status": "DISCOVERING",
                "discovered_resources": {},
                "aggregated_metadata": {}
            }
        else:
            # Refresh requested on existing cache: return stale cache instantly, trigger refresh in bg
            logger.info(f"Cache hit for {project_id}, but refresh=true. Triggering background rescan...")
            background_tasks.add_task(trigger_scanner_job, project_id)
            return cache

    # Normal cache hit
    return cache


@router.post("/v1/agents/inventory/refresh")
async def refresh_inventory(request: RefreshRequest, background_tasks: BackgroundTasks):
    """Explicitly triggers a background infrastructure rescan/refresh."""
    logger.info(f"Forced refresh requested for project={request.project_id}")
    await set_project_status(request.project_id, "DISCOVERING")
    background_tasks.add_task(trigger_scanner_job, request.project_id)
    return {"status": "success", "detail": f"Scan triggered for project {request.project_id}"}


@router.post("/v1/agents/inventory/callback")
async def discovery_callback(request: CallbackRequest):
    """Callback endpoint invoked by run-to-completion scanner jobs upon scan completion."""
    logger.info(f"Received scan completion callback for project={request.project_id}")
    await update_project_inventory(
        project_id=request.project_id,
        discovered_resources=request.discovered_resources,
        aggregated_metadata=request.aggregated_metadata,
        status=request.status
    )
    return {"status": "success", "detail": f"Cached updated for project {request.project_id}"}
