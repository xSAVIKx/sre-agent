"""Unit tests for Firestore Connection Strategy."""

import os
import tempfile
import shutil
import unittest
from unittest import mock
from agent.firestore_strategy import (
    FirestoreConnectionStrategy,
    MOCK_FIRESTORE_DB,
)


class TestFirestoreConnectionStrategy(unittest.IsolatedAsyncioTestCase):
    """Unit tests for FirestoreConnectionStrategy using unittest."""

    def setUp(self) -> None:
        """Resets the mock Firestore database before each test."""
        super().setUp()
        MOCK_FIRESTORE_DB.clear()

    def tearDown(self) -> None:
        """Resets the mock Firestore database after each test."""
        MOCK_FIRESTORE_DB.clear()
        super().tearDown()

    async def test_firestore_strategy_enter_no_conv_id(self) -> None:
        """Verifies that __aenter__ behaves correctly when no conversation_id is provided."""
        save_dir = tempfile.mkdtemp(prefix="test_strategy_")
        mock_local = mock.MagicMock()
        mock_local.__aenter__ = mock.AsyncMock()
        mock_local.__aexit__ = mock.AsyncMock()
        mock_local.connect.return_value = mock.MagicMock(conversation_id="new-id-123")

        strategy = FirestoreConnectionStrategy(
            local_strategy=mock_local,
            conversation_id=None,
            save_dir=save_dir,
            mock_mode=True,
        )

        # Enter the strategy
        await strategy.__aenter__()

        # Verify that underlying local strategy enter was called
        mock_local.__aenter__.assert_awaited_once()
        
        # Verify no files were created because there was no conversation_id to restore
        assert len(os.listdir(save_dir)) == 0

        # Exit the strategy
        await strategy.__aexit__(None, None, None)
        mock_local.__aexit__.assert_awaited_once()

        # Cleanup test dir
        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)

    async def test_firestore_strategy_enter_with_conv_id(self) -> None:
        """Verifies that __aenter__ restores existing session files from mock DB."""
        save_dir = tempfile.mkdtemp(prefix="test_strategy_")
        conv_id = "existing-conv-id"
        file_content = b"serialized_protobuf_state"
        filename = "traj-existing-conv-id"

        # Seed mock DB
        MOCK_FIRESTORE_DB[conv_id] = {
            "conversation_id": conv_id,
            "files": {
                filename: file_content,
            },
        }

        mock_local = mock.MagicMock()
        mock_local.__aenter__ = mock.AsyncMock()
        mock_local.__aexit__ = mock.AsyncMock()
        mock_local.connect.return_value = mock.MagicMock(conversation_id=conv_id)

        strategy = FirestoreConnectionStrategy(
            local_strategy=mock_local,
            conversation_id=conv_id,
            save_dir=save_dir,
            mock_mode=True,
        )

        # Enter strategy
        await strategy.__aenter__()

        # Verify that local file was successfully restored
        restored_file = os.path.join(save_dir, filename)
        assert os.path.exists(restored_file)
        with open(restored_file, "rb") as f:
            assert f.read() == file_content

        # Exit strategy
        await strategy.__aexit__(None, None, None)

        # Cleanup test dir
        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)

    async def test_firestore_strategy_exit_saves_and_cleans(self) -> None:
        """Verifies that __aexit__ saves local files to mock DB and deletes save_dir."""
        save_dir = tempfile.mkdtemp(prefix="test_strategy_")
        conv_id = "another-conv-id"
        filename = f"traj-{conv_id}"
        file_content = b"new_state_data_after_chat"

        # Simulate harness writing state file to disk
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, filename), "wb") as f:
            f.write(file_content)

        mock_local = mock.MagicMock()
        mock_local.__aenter__ = mock.AsyncMock()
        mock_local.__aexit__ = mock.AsyncMock()
        mock_local.connect.return_value = mock.MagicMock(conversation_id=conv_id)

        strategy = FirestoreConnectionStrategy(
            local_strategy=mock_local,
            conversation_id=conv_id,
            save_dir=save_dir,
            mock_mode=True,
        )

        # Exit strategy
        await strategy.__aexit__(None, None, None)

        # Verify underlying local strategy exit was called
        mock_local.__aexit__.assert_awaited_once()

        # Verify data was uploaded/saved to the mock database
        assert conv_id in MOCK_FIRESTORE_DB
        assert MOCK_FIRESTORE_DB[conv_id]["files"][filename] == file_content

        # Verify local save_dir was cleaned up and deleted
        assert not os.path.exists(save_dir)


if __name__ == "__main__":
    unittest.main()
