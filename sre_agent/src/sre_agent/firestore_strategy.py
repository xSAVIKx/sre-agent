"""Firestore persistence utilities for private SRE diagnostic sessions.
"""

import os
import logging
import datetime
from typing import Any

from sre_common import retry_async, otel_trace

logger = logging.getLogger("sre_agent.firestore_strategy")

IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1", "yes")

# Local fallback memory db
MOCK_SRE_DB: dict[str, dict[str, Any]] = {}


async def _get_db() -> Any:
    """Helper to initialize Async Firestore Client."""
    if IS_MOCK and "FIRESTORE_EMULATOR_HOST" not in os.environ:
        return None
    try:
        from google.cloud import firestore
        return firestore.AsyncClient()
    except Exception as e:
        logger.warning(f"Failed to load Firestore client, using in-memory mock: {e}")
        return None


@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("firestore_strategy.get_sre_session")
async def get_sre_session(conversation_id: str) -> dict[str, Any] | None:
    """Loads a private SRE session history from Firestore or mock DB."""
    db = await _get_db()
    if db is None:
        return MOCK_SRE_DB.get(conversation_id)

    try:
        doc_ref = db.collection("sre_workflow_sessions").document(conversation_id)
        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        logger.error(f"Failed to read SRE session from Firestore: {e}")
    return None


@retry_async(max_retries=3, initial_delay=1.0)
@otel_trace("firestore_strategy.save_sre_session")
async def save_sre_session(conversation_id: str, history: list[dict[str, Any]]) -> None:
    """Saves/appends private SRE reasoning steps to Firestore or mock DB."""
    db = await _get_db()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    payload = {
        "conversation_id": conversation_id,
        "history": history,
        "updated_at": now
    }

    if db is None:
        MOCK_SRE_DB[conversation_id] = payload
        return

    try:
        doc_ref = db.collection("sre_workflow_sessions").document(conversation_id)
        await doc_ref.set(payload, merge=True)
        logger.info(f"Successfully saved private SRE session {conversation_id} in Firestore")
    except Exception as e:
        logger.error(f"Failed to write SRE session to Firestore: {e}")
