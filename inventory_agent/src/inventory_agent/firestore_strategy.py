"""Firestore access utilities for Inventory caching.
"""

import os
import logging
import datetime
from typing import Any
from sre_common import retry_async, otel_trace

logger = logging.getLogger("inventory_agent.firestore_strategy")

# Determine if we should run in mock/simulator mode
IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# In-memory database fallback when running locally without emulator or project config
MOCK_INVENTORY_DB: dict[str, dict[str, Any]] = {}


async def _get_db() -> Any:
    """Helper to initialize Async Firestore Client.
    
    If FIRESTORE_EMULATOR_HOST is set, Firestore client automatically connects to it.
    """
    if IS_MOCK and "FIRESTORE_EMULATOR_HOST" not in os.environ:
        return None
    try:
        from google.cloud import firestore
        return firestore.AsyncClient()
    except Exception as e:
        logger.warning(f"Failed to load Firestore client, using in-memory mock: {e}")
        return None


@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("inventory_firestore.get_project_inventory")
async def get_project_inventory(project_id: str) -> dict[str, Any] | None:
    """Loads a project's cached inventory from Firestore or local mock database."""
    db = await _get_db()
    if db is None:
        logger.info(f"[Mock DB] Reading inventory for {project_id}")
        return MOCK_INVENTORY_DB.get(project_id)

    try:
        doc_ref = db.collection("project_inventories").document(project_id)
        doc = await doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            logger.info(f"Retrieved Firestore inventory cache for {project_id}")
            return data
    except Exception as e:
        logger.error(f"Failed to read project inventory from Firestore: {e}")

    return None


@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("inventory_firestore.update_project_inventory")
async def update_project_inventory(
    project_id: str,
    discovered_resources: dict[str, Any],
    aggregated_metadata: dict[str, Any],
    status: str = "ACTIVE"
) -> None:
    """Saves/updates a project's cached inventory in Firestore or mock database."""
    db = await _get_db()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    payload = {
        "project_id": project_id,
        "discovered_resources": discovered_resources,
        "aggregated_metadata": aggregated_metadata,
        "status": status,
        "last_update_time": now
    }

    if db is None:
        logger.info(f"[Mock DB] Saving inventory for {project_id}")
        MOCK_INVENTORY_DB[project_id] = payload
        return

    try:
        doc_ref = db.collection("project_inventories").document(project_id)
        await doc_ref.set(payload, merge=True)
        logger.info(f"Successfully cached inventory for {project_id} in Firestore")
    except Exception as e:
        logger.error(f"Failed to write project inventory to Firestore: {e}")


@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("inventory_firestore.set_project_status")
async def set_project_status(project_id: str, status: str) -> None:
    """Helper to update a project's scanning status in Firestore/Mock DB."""
    db = await _get_db()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if db is None:
        if project_id in MOCK_INVENTORY_DB:
            MOCK_INVENTORY_DB[project_id]["status"] = status
            MOCK_INVENTORY_DB[project_id]["last_update_time"] = now
        else:
            MOCK_INVENTORY_DB[project_id] = {
                "project_id": project_id,
                "status": status,
                "discovered_resources": {},
                "aggregated_metadata": {},
                "last_update_time": now
            }
        return

    try:
        doc_ref = db.collection("project_inventories").document(project_id)
        await doc_ref.set({
            "status": status,
            "last_update_time": now
        }, merge=True)
        logger.info(f"Updated status for {project_id} to {status} in Firestore")
    except Exception as e:
        logger.error(f"Failed to set status for {project_id}: {e}")
