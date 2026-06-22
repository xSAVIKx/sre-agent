"""Unit tests for Itinerary Enrichment and Seeding.
"""

import unittest
from unittest import mock
from sre_agent.itinerary import get_embedding, seed_templates_if_empty, find_matching_template, DEFAULT_TEMPLATES

class TestItineraryEnrichment(unittest.IsolatedAsyncioTestCase):
    """Unit tests for itinerary mapping and seeding."""

    async def test_get_embedding_mock(self) -> None:
        """Verifies get_embedding returns zero vector when IS_MOCK is True."""
        with mock.patch("sre_agent.itinerary.IS_MOCK", True):
            embedding = await get_embedding("test text")
            self.assertEqual(len(embedding), 768)
            self.assertTrue(all(val == 0.0 for val in embedding))

    async def test_find_matching_template_mock(self) -> None:
        """Verifies find_matching_template mock fallback behavior."""
        with mock.patch("sre_agent.itinerary.IS_MOCK", True):
            # Test Cloud Run template lookup
            template = await find_matching_template(None, "cloud_run_revision", "service: test, type: cloud_run_revision")
            self.assertIsNotNone(template)
            self.assertEqual(template["resource_type"], "cloud_run_revision")
            self.assertIn("Cloud Run", template["name"])
            
            # Test Datastore template lookup
            template_ds = await find_matching_template(None, "datastore_database", "database: (default), type: datastore_database")
            self.assertIsNotNone(template_ds)
            self.assertEqual(template_ds["resource_type"], "datastore_database")
            
            # Test non-existent resource type fallback
            template_none = await find_matching_template(None, "non_existent_type", "query")
            self.assertIsNone(template_none)

    async def test_seed_templates_if_empty(self) -> None:
        """Verifies seed_templates_if_empty interacts correctly with Firestore AsyncClient mocks."""
        mock_db = mock.MagicMock()
        mock_collection = mock.MagicMock()
        mock_query = mock.MagicMock()
        
        mock_db.collection.return_value = mock_collection
        mock_collection.limit.return_value = mock_query
        
        # Scenario 1: Collection is NOT empty (already seeded)
        mock_query.get = mock.AsyncMock(return_value=[mock.MagicMock()])
        
        await seed_templates_if_empty(mock_db)
        
        # Verify collection.document was NOT called since it was already seeded
        mock_collection.document.assert_not_called()
        
        # Scenario 2: Collection IS empty (needs seeding)
        mock_query.get = mock.AsyncMock(return_value=[])
        
        mock_document = mock.MagicMock()
        mock_document.set = mock.AsyncMock()
        mock_collection.document.return_value = mock_document
        
        with mock.patch("sre_agent.itinerary.IS_MOCK", True):
            await seed_templates_if_empty(mock_db)
            
        # Verify that collection.document and document.set were called for each default template
        self.assertEqual(mock_collection.document.call_count, len(DEFAULT_TEMPLATES))
        self.assertEqual(mock_document.set.call_count, len(DEFAULT_TEMPLATES))
