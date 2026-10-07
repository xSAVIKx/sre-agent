"""Import smoke test for every module in the workspace.

`agent/` and `sre_agent/` have unit tests. `app/`, `inventory_agent/`,
`sre_common/` and the `.agents/skills/sre_incident_solver/` mirror do not, so nothing in
CI used to import them at all - which is how
`inventory_agent/discovery.py` came to annotate two functions with `Any` without
importing it. That is a hard `NameError` on Python 3.11:

    def run_gcp_discovery(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    NameError: name 'Any' is not defined. Did you mean: 'any'?

It only looked fine because the venv resolves to 3.14, where PEP 649 defers
annotation evaluation. Importing every module on the lowest supported Python is
the cheapest possible guard against that whole class of bug.
"""

import importlib
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# package directory -> importable module names inside it.
PACKAGES: dict[str, tuple[str, ...]] = {
    "agent/src": ("agent.config", "agent.firestore_strategy", "agent.main", "agent.routes"),
    "sre_agent/src": (
        "sre_agent.config",
        "sre_agent.firestore_strategy",
        "sre_agent.gcp_tools",
        "sre_agent.itinerary",
        "sre_agent.main",
        "sre_agent.registry",
        "sre_agent.routes",
        "sre_agent.sre_workflow",
    ),
    "inventory_agent/src": (
        "inventory_agent.config",
        "inventory_agent.discovery",
        "inventory_agent.firestore_strategy",
        "inventory_agent.main",
        "inventory_agent.routes",
    ),
    "sre_common/src": (
        "sre_common",
        "sre_common.logging",
        "sre_common.middleware",
        "sre_common.otel",
        "sre_common.retry",
    ),
    ".": ("app.main",),
    # AGENTS.md calls this a portable copy of the diagnostics engine that has to
    # keep working, and it is the one tree with no tests of its own. Its three
    # modules import cleanly today; this keeps them that way.
    ".agents/skills": (
        "sre_incident_solver.config",
        "sre_incident_solver.firestore_strategy",
        "sre_incident_solver.gcp_tools",
        "sre_incident_solver.incidents",
        "sre_incident_solver.inventory_client",
        "sre_incident_solver.itinerary",
        "sre_incident_solver.registry",
        "sre_incident_solver.sre_workflow",
    ),
}


class TestWorkspaceImports(unittest.TestCase):
    """Every workspace module must import cleanly on the running interpreter."""

    @classmethod
    def setUpClass(cls) -> None:
        """Puts every package's source root on `sys.path`."""
        for src in PACKAGES:
            path = str(REPO_ROOT / src)
            if path not in sys.path:
                sys.path.insert(0, path)

    def test_every_module_imports(self) -> None:
        """Imports each module and reports all failures at once."""
        failures: list[str] = []
        for modules in PACKAGES.values():
            for name in modules:
                try:
                    importlib.import_module(name)
                except Exception as exc:
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual([], failures, "modules failed to import:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
