"""Contract tests for the `google-antigravity` API surface the Orchestrator uses.

`agent/src/agent/config.py` wraps every Antigravity import in a `try/except
ImportError` and falls back to hand-written mock classes. That fallback is what
keeps local development pleasant, but it also means the rest of the test suite
runs entirely against the mocks and would stay green through an upstream rename.

`google-antigravity` is a 0.x SDK that removes public API in patch releases -
`LocalAgentConfig.gemini_config` and `Agent.register_hook` both disappeared in
0.1.4 - so these tests assert against the *real* installed package and fail loudly
when the surface this repo depends on moves.
"""

import contextlib
import importlib
import inspect
import os
import tempfile
import unittest
from unittest import mock


class TestAntigravityContract(unittest.TestCase):
    """Pins the Antigravity symbols and signatures `agent/` depends on."""

    def test_imports_used_by_config_resolve(self) -> None:
        """Every symbol in the `try` block of `agent.config` must exist upstream."""
        from google.antigravity import Agent, LocalAgentConfig  # noqa: F401
        from google.antigravity.hooks.policy import allow, ask_user, deny  # noqa: F401
        from google.antigravity.hooks.hooks import HookContext, OnToolErrorHook  # noqa: F401
        from google.antigravity.types import Text, Thought, ToolCall, ToolResult  # noqa: F401

    def test_imports_used_by_firestore_strategy_resolve(self) -> None:
        """Every symbol the Firestore session strategy imports must exist upstream."""
        from google.antigravity.connections import connection  # noqa: F401
        from google.antigravity.connections.local.local_connection import LocalConnectionStrategy  # noqa: F401
        from google.antigravity.connections.local.local_connection_config import LocalAgentConfig  # noqa: F401

    def test_step_type_used_by_session_replay_resolves(self) -> None:
        """`agent.routes` rehydrates Firestore history into `types.Step`."""
        from google.antigravity.types import Step

        for field in ("source", "step_index", "type", "status", "content"):
            self.assertIn(field, Step.model_fields)

    def test_local_agent_config_accepts_the_fields_we_set(self) -> None:
        """`load_agent_config` constructs a config from these four fields."""
        from google.antigravity.connections.local.local_connection_config import LocalAgentConfig

        for field in ("system_instructions", "tools", "policies", "hooks", "conversation_id"):
            self.assertIn(field, LocalAgentConfig.model_fields)

    def test_firestore_strategy_forwards_every_local_strategy_field(self) -> None:
        """`FirestoreAgentConfig.create_strategy` must stay in step with the SDK.

        The subclass reimplements `LocalAgentConfig.create_strategy` so it can wrap
        the local strategy in the Firestore backup/restore strategy. If upstream adds
        or renames a `LocalConnectionStrategy` parameter, the override silently stops
        forwarding it (or raises `TypeError`). Assert the two agree.
        """
        from google.antigravity.connections.local.local_connection import LocalConnectionStrategy

        from agent.firestore_strategy import FirestoreAgentConfig

        upstream = set(inspect.signature(LocalConnectionStrategy.__init__).parameters) - {"self"}
        source = inspect.getsource(FirestoreAgentConfig.create_strategy)
        forwarded = {name for name in upstream if f"{name}=" in source}
        self.assertEqual(
            upstream,
            forwarded,
            "FirestoreAgentConfig.create_strategy does not forward: "
            f"{sorted(upstream - forwarded)}",
        )

    @contextlib.contextmanager
    def _agent_config_as_deployed(self):
        """Reimports `agent.config` the way the deployed Orchestrator loads it.

        `HAS_ANTIGRAVITY` is decided at import time and is False in CI (no
        `GEMINI_API_KEY`), so the module-level fallbacks shadow the real `deny`
        and `allow` and `load_firestore_agent_config` never takes its Firestore
        branch. Setting the variable and reloading is the only way to exercise
        the code that actually runs in production. No key is needed to *build* a
        config - only `Agent.__aenter__` would call out.

        The module is reloaded again on the way out so the rest of the suite
        keeps the mock fallbacks it expects.
        """
        import agent.config

        try:
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "not-used-no-call-is-made"}):
                importlib.reload(agent.config)
                self.assertTrue(agent.config.HAS_ANTIGRAVITY)
                yield agent.config
        finally:
            importlib.reload(agent.config)

    def test_orchestrator_policy_survives_the_trip_into_the_harness(self) -> None:
        """The deny-by-default posture must reach the harness, not just the config.

        `test_firestore_strategy_forwards_every_local_strategy_field` reads the
        source of the override and only proves the `policies=` line is *present*.
        It stays green if that line is rewritten to `policies=None`, and so does
        everything else: `Agent.__aenter__` validates `self._config.policies`,
        which is still populated, while the harness quietly starts with the SDK
        default (every builtin tool enabled) instead of
        `[deny("*"), allow("diagnose_sre")]`. Assert the effective posture.

        This reaches through `FirestoreConnectionStrategy._local_strategy` into
        `LocalConnectionStrategy._build_harness_config()`, both private. That is
        a deliberate trade: the policy proto is the only place the *effective*
        posture is observable without a live harness and an API key, and the SDK
        exercises `_build_harness_config()` the same way in its own tests. If a
        future google-antigravity moves either name this test breaks loudly on
        an AttributeError - which is the correct outcome for a contract test.
        Re-point it at whatever replaces them; do not weaken it back into a
        source-text check.
        """
        with self._agent_config_as_deployed() as config_module:
            from agent.firestore_strategy import FirestoreAgentConfig
            from google.antigravity.proto import localharness_pb2

            config = config_module.load_firestore_agent_config()
            self.assertIsInstance(config, FirestoreAgentConfig)

            with tempfile.TemporaryDirectory() as save_dir:
                # Keep the harness's session scratch out of the working tree.
                config.save_dir = save_dir
                strategy = config.create_strategy(tool_runner=None, hook_runner=None)
                harness_config = strategy._local_strategy._build_harness_config()

            self.assertEqual(
                {rule.tool: rule.decision for rule in harness_config.policy_config.rules},
                {
                    "*": localharness_pb2.POLICY_DECISION_DENY,
                    "diagnose_sre": localharness_pb2.POLICY_DECISION_ALLOW,
                },
                "The Orchestrator must reach the harness deny-by-default with only diagnose_sre allowed",
            )

    def test_agent_chat_is_still_an_async_context_manager_protocol(self) -> None:
        """`simulate_incident.py` and `agent.routes` rely on `async with Agent(cfg)`."""
        from google.antigravity import Agent

        self.assertTrue(hasattr(Agent, "__aenter__"))
        self.assertTrue(hasattr(Agent, "__aexit__"))
        self.assertTrue(inspect.iscoroutinefunction(Agent.chat))
        self.assertTrue(hasattr(Agent, "conversation_id"))


if __name__ == "__main__":
    unittest.main()
