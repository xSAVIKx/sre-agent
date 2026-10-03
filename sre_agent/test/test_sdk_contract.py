"""Contract tests for the `google-adk` API surface the SRE workflow uses.

`sre_agent/src/sre_agent/sre_workflow.py` guards its ADK imports with a
`try/except ImportError` and substitutes mock `Agent`/`Workflow`/`node` classes.
Nothing else in the test suite touches the real package, so an upstream rename
would degrade the workflow to the simulated fallback without failing a test.

These tests assert against the installed `google-adk` instead.
"""

import inspect
import unittest


class TestAdkContract(unittest.TestCase):
    """Pins the ADK symbols and signatures `sre_agent/` depends on."""

    def test_imports_used_by_sre_workflow_resolve(self) -> None:
        """Every symbol in the `try` block of `sre_agent.sre_workflow` must exist."""
        from google.adk import Agent, Context, Workflow  # noqa: F401
        from google.adk.workflow import START, node  # noqa: F401

    def test_runtime_imports_resolve(self) -> None:
        """`_run_adk_diagnostics` imports these lazily, inside the function body."""
        from google.adk.runners import Runner  # noqa: F401
        from google.adk.sessions import InMemorySessionService  # noqa: F401
        from google.genai import types  # noqa: F401

    def test_adk_agent_accepts_the_fields_we_set(self) -> None:
        """`trace_analyzer` / `log_correlator` are built from these fields."""
        from google.adk import Agent

        for field in ("name", "instruction", "model", "tools"):
            self.assertIn(field, Agent.model_fields)

    def test_runner_still_accepts_a_workflow_node(self) -> None:
        """The workflow is handed to `Runner` as `node=`, not `agent=`."""
        from google.adk.runners import Runner

        params = inspect.signature(Runner.__init__).parameters
        self.assertIn("node", params)
        self.assertIn("app_name", params)
        self.assertIn("session_service", params)

    def test_node_default_parameter_binding_passes_node_input_through(self) -> None:
        """ADK 2.7 added `parameter_binding` to `@node`; the default must stay `state`.

        `fetch_telemetry(ctx, node_input)` reads its argument positionally. In
        `'state'` mode the `node_input` parameter is passed through directly; in
        `'node_input'` mode it would be looked up as a key inside a dict instead.
        """
        from google.adk.workflow import node

        binding = inspect.signature(node).parameters.get("parameter_binding")
        if binding is not None:
            self.assertEqual("state", binding.default)

    def test_create_session_signature(self) -> None:
        """`_run_adk_diagnostics` creates the session explicitly before running."""
        from google.adk.sessions import InMemorySessionService

        params = inspect.signature(InMemorySessionService.create_session).parameters
        self.assertIn("app_name", params)
        self.assertIn("user_id", params)


if __name__ == "__main__":
    unittest.main()
