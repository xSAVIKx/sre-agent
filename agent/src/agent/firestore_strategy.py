"""Firestore Connection Strategy for Antigravity Session Persistence.

This module provides a custom ConnectionStrategy and AgentConfig that automatically
backs up and restores agent session files in Google Cloud Firestore, enabling stateless
session resumption across server instances.
"""

import os
import shutil
import logging
from typing import Any
from google.antigravity.connections import connection
from google.antigravity.connections.local.local_connection_config import LocalAgentConfig
from google.antigravity.connections.local.local_connection import LocalConnectionStrategy

logger = logging.getLogger("sre_agent.firestore_strategy")

# Define simple otel_trace fallback decorator locally to avoid dependency on skills folder
def otel_trace(span_name: str):
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Global in-memory DB for local testing/mock mode
MOCK_FIRESTORE_DB: dict[str, dict[str, Any]] = {}


class FirestoreConnectionStrategy(connection.ConnectionStrategy):
    """A ConnectionStrategy that wraps the LocalConnectionStrategy.

    Restores session state files from Google Cloud Firestore on startup and
    uploads any updated files to Firestore on shutdown.
    """

    def __init__(
        self,
        local_strategy: LocalConnectionStrategy,
        conversation_id: str | None,
        save_dir: str,
        collection_name: str = "agent_sessions",
        mock_mode: bool = False,
        prompt: str | None = None,
    ) -> None:
        """Initializes the FirestoreConnectionStrategy.

        Args:
            local_strategy: The underlying LocalConnectionStrategy to execute.
            conversation_id: Optional ID to resume an existing conversation.
            save_dir: The local directory where session files are stored temporarily.
            collection_name: The Firestore collection name.
            mock_mode: If True, uses the local in-memory DB instead of calling Firestore APIs.
            prompt: The user prompt to save as session metadata.
        """
        self._local_strategy = local_strategy
        self.conversation_id = conversation_id
        self._save_dir = save_dir
        self._collection_name = collection_name
        self._mock_mode = mock_mode
        self._db: Any = None
        self.prompt = prompt
        self._existing_prompt: str | None = None

    def connect(self) -> connection.Connection:
        """Returns the established local Connection."""
        return self._local_strategy.connect()

    @otel_trace("firestore_strategy.connect")
    async def __aenter__(self) -> "FirestoreConnectionStrategy":
        """Downloads session files from Firestore and starts the local strategy."""
        # 1. Setup Firestore client if not in mock mode
        if not self._mock_mode:
            try:
                from google.cloud import firestore
                self._db = firestore.AsyncClient()
            except Exception as e:
                logger.warning(
                    f"Failed to initialize Firestore client, falling back to mock mode: {e}"
                )
                self._mock_mode = True

        # 2. Download files from Firestore/Mock DB
        if self.conversation_id:
            logger.info(f"Restoring session history for conversation_id={self.conversation_id}")
            session_doc = None
            if self._mock_mode:
                session_doc = MOCK_FIRESTORE_DB.get(self.conversation_id)
            else:
                try:
                    doc_ref = self._db.collection(self._collection_name).document(self.conversation_id)
                    doc = await doc_ref.get()
                    if doc.exists:
                        session_doc = doc.to_dict()
                except Exception as e:
                    logger.error(f"Failed to retrieve document from Firestore: {e}")

            if session_doc:
                if "prompt" in session_doc:
                    self._existing_prompt = session_doc["prompt"]
                if "files" in session_doc:
                    os.makedirs(self._save_dir, exist_ok=True)
                    for filename, file_data in session_doc["files"].items():
                        filepath = os.path.join(self._save_dir, filename)
                        # Retrieve the binary bytes
                        if isinstance(file_data, str):
                            try:
                                import base64
                                file_bytes = base64.b64decode(file_data)
                            except Exception:
                                file_bytes = file_data.encode("utf-8")
                        else:
                            file_bytes = file_data

                        with open(filepath, "wb") as f:
                            f.write(file_bytes)
                        logger.info(f"Restored file {filename} ({len(file_bytes)} bytes)")

        # 3. Enter the local connection strategy
        await self._local_strategy.__aenter__()
        return self

    @otel_trace("firestore_strategy.disconnect")
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Uploads updated session files to Firestore and tears down local resources."""
        # 1. Retrieve conversation_id from the established connection while it's active
        try:
            conn = self._local_strategy.connect()
            active_conversation_id = self.conversation_id or conn.conversation_id
        except Exception:
            active_conversation_id = self.conversation_id

        # 2. Wait for local strategy exit to flush all writes to disk
        await self._local_strategy.__aexit__(exc_type, exc_val, exc_tb)

        if not active_conversation_id:
            logger.warning("No conversation ID resolved. Skipping session persistence upload.")
            return

        self.conversation_id = active_conversation_id

        # 3. Gather session files to upload
        files_dict: dict[str, bytes] = {}
        if os.path.exists(self._save_dir):
            for filename in os.listdir(self._save_dir):
                filepath = os.path.join(self._save_dir, filename)
                if os.path.isfile(filepath):
                    try:
                        with open(filepath, "rb") as f:
                            files_dict[filename] = f.read()
                        logger.info(f"Staged file {filename} ({os.path.getsize(filepath)} bytes)")
                    except Exception as e:
                        logger.error(f"Failed to read file {filepath}: {e}")

        # 4. Upload session files to Firestore/Mock DB
        if files_dict:
            import datetime
            resolved_prompt = self._existing_prompt or self.prompt
            if self._mock_mode:
                session_data = {
                    "conversation_id": active_conversation_id,
                    "files": files_dict,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc),
                    "prompt": resolved_prompt,
                }
                if active_conversation_id not in MOCK_FIRESTORE_DB:
                    MOCK_FIRESTORE_DB[active_conversation_id] = {}
                MOCK_FIRESTORE_DB[active_conversation_id].update(session_data)
                logger.info(f"[Mock] Saved session state for {active_conversation_id}")
            else:
                try:
                    from google.cloud import firestore
                    session_data = {
                        "conversation_id": active_conversation_id,
                        "files": files_dict,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                        "prompt": resolved_prompt,
                    }
                    doc_ref = self._db.collection(self._collection_name).document(active_conversation_id)
                    await doc_ref.set(session_data, merge=True)
                    logger.info(f"Uploaded session state to Firestore for {active_conversation_id}")
                except Exception as e:
                    logger.exception(f"Failed to upload session state to Firestore: {e}")

        # 5. Clean up temporary save_dir
        try:
            if os.path.exists(self._save_dir):
                shutil.rmtree(self._save_dir)
                logger.info(f"Successfully cleaned up local temporary save_dir: {self._save_dir}")
        except Exception as e:
            logger.warning(f"Cleanup of save_dir failed: {e}")


class FirestoreAgentConfig(LocalAgentConfig):
    """Configuration class for the Firestore remote session strategy.

    Extends LocalAgentConfig to wrap the LocalConnectionStrategy with
    FirestoreConnectionStrategy remote backup/restore capability.
    """
    prompt: str | None = None

    def create_strategy(
        self,
        *,
        tool_runner: Any,
        hook_runner: Any,
    ) -> connection.ConnectionStrategy:
        """Creates a FirestoreConnectionStrategy instance for SRE diagnostics."""
        save_dir = self._get_or_create_save_dir()

        local_strategy = LocalConnectionStrategy(
            tool_runner=tool_runner,
            hook_runner=hook_runner,
            gemini_config=self.gemini_config,
            system_instructions=self._get_system_instructions(),
            capabilities_config=self.capabilities,
            conversation_id=self.conversation_id,
            save_dir=save_dir,
            workspaces=self.workspaces,
            app_data_dir=self.app_data_dir,
            skills_paths=self.skills_paths,
        )

        mock_gcp = os.getenv("MOCK_GCP", "false").lower() in ("true", "1")

        return FirestoreConnectionStrategy(
            local_strategy=local_strategy,
            conversation_id=self.conversation_id,
            save_dir=save_dir,
            mock_mode=mock_gcp,
            prompt=getattr(self, "prompt", None),
        )
