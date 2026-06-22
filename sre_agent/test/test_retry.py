"""Unit tests for the shared retry utilities.
"""

import unittest
import asyncio
from unittest import mock
import httpx

from sre_common.retry import is_transient_error, retry_async, retry_sync

class MockTransientException(Exception):
    def __init__(self, message="Simulated transient error"):
        super().__init__(message)
        self.status_code = 429

class MockNonTransientException(Exception):
    pass

class TestRetryUtilities(unittest.IsolatedAsyncioTestCase):
    """Tests the transient error detection and backoff retry logic."""

    def test_is_transient_error(self) -> None:
        """Verifies is_transient_error correctly identifies transient vs permanent failures."""
        # 1. Test status code attribute
        self.assertTrue(is_transient_error(MockTransientException()))
        
        # 2. Test generic exception naming
        class ResourceExhausted(Exception): pass
        class ServiceUnavailable(Exception): pass
        self.assertTrue(is_transient_error(ResourceExhausted("Rate exceeded")))
        self.assertTrue(is_transient_error(ServiceUnavailable("Unavailable")))
        
        # 3. Test message substring matches
        self.assertTrue(is_transient_error(Exception("429 rate limit reached")))
        self.assertTrue(is_transient_error(Exception("quota exceeded")))
        
        # 4. Test non-transient exceptions
        self.assertFalse(is_transient_error(MockNonTransientException("Fatal database error")))
        self.assertFalse(is_transient_error(ValueError("Invalid argument")))

    async def test_retry_async_success(self) -> None:
        """Verifies retry_async successfully retries and eventually succeeds."""
        call_count = 0

        @retry_async(max_retries=3, initial_delay=0.01, jitter=False)
        async def mock_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise MockTransientException()
            return "success"

        result = await mock_async_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

    async def test_retry_async_exhausted(self) -> None:
        """Verifies retry_async raises the last exception after max_retries are exhausted."""
        call_count = 0

        @retry_async(max_retries=2, initial_delay=0.01, jitter=False)
        async def mock_async_func():
            nonlocal call_count
            call_count += 1
            raise MockTransientException(f"Fail {call_count}")

        with self.assertRaises(MockTransientException) as ctx:
            await mock_async_func()
        
        self.assertEqual(str(ctx.exception), "Fail 3")  # initial attempt + 2 retries = 3 total attempts
        self.assertEqual(call_count, 3)

    async def test_retry_async_non_transient(self) -> None:
        """Verifies retry_async immediately raises non-transient exceptions without retrying."""
        call_count = 0

        @retry_async(max_retries=3, initial_delay=0.01, jitter=False)
        async def mock_async_func():
            nonlocal call_count
            call_count += 1
            raise MockNonTransientException("Fatal error")

        with self.assertRaises(MockNonTransientException):
            await mock_async_func()
        
        self.assertEqual(call_count, 1)

    def test_retry_sync_success(self) -> None:
        """Verifies retry_sync successfully retries and eventually succeeds."""
        call_count = 0

        @retry_sync(max_retries=3, initial_delay=0.01, jitter=False)
        def mock_sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise MockTransientException()
            return "success"

        result = mock_sync_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

    def test_retry_sync_non_transient(self) -> None:
        """Verifies retry_sync immediately raises non-transient exceptions."""
        call_count = 0

        @retry_sync(max_retries=3, initial_delay=0.01, jitter=False)
        def mock_sync_func():
            nonlocal call_count
            call_count += 1
            raise MockNonTransientException()

        with self.assertRaises(MockNonTransientException):
            mock_sync_func()
        self.assertEqual(call_count, 1)
