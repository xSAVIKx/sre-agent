"""Tests for environment-driven Orchestrator configuration."""

import importlib
import os
import unittest
from unittest import mock


class TestGeminiKeyDetection(unittest.TestCase):
    """An empty GEMINI_API_KEY must select the simulated agent, not the real SDK."""

    def tearDown(self) -> None:
        import agent.config

        importlib.reload(agent.config)

    def test_empty_key_counts_as_missing(self) -> None:
        import agent.config

        # docker-compose passes `GEMINI_API_KEY=${GEMINI_API_KEY:-}`, i.e. an empty string.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            importlib.reload(agent.config)
            self.assertFalse(agent.config.HAS_ANTIGRAVITY)


if __name__ == "__main__":
    unittest.main()
