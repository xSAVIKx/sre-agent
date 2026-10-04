"""Checks a workshop step: `uv run workshop/check.py <step>` (or `all`).

Each step has a small set of tests that are red while its `TODO(step-N)` is open
and green once it is solved. Step 0 checks your environment instead.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# step -> (title, [(source roots on PYTHONPATH, test dir, unittest ids)])
STEP_TESTS: dict[int, tuple[str, list[tuple[list[str], str, list[str]]]]] = {
    1: (
        "Find the bottleneck",
        [
            (
                ["sre_agent/src"],
                "sre_agent/test",
                [
                    "test_gcp_tools.TestGcpToolsMetrics.test_analyze_trace_cascade_mock",
                    "test_gcp_tools.TestGcpToolsMetrics.test_analyze_trace_cascade_overlapping_children_and_duplicate_span",
                ],
            )
        ],
    ),
    2: ("Feed the metrics", [([], "test", ["test_app_mock_metrics"])]),
    3: ("Give the agent its tools", [(["sre_agent/src"], "sre_agent/test", ["test_workflow_agents"])]),
    4: (
        "Lock the Orchestrator down",
        [
            (
                ["agent/src"],
                "agent/test",
                [
                    "test_chat_routing.TestMockPolicyEvaluation",
                    "test_chat_routing.TestChatRouting",
                    "test_sdk_contract.TestAntigravityContract.test_orchestrator_policy_survives_the_trip_into_the_harness",
                ],
            )
        ],
    ),
    5: ("Show the severity", [(["sre_agent/src"], "sre_agent/test", ["test_severity_badge"])]),
}

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def check_environment() -> bool:
    """Step 0: the tools and packages every later step needs."""
    ok = True
    print(f"Python {sys.version.split()[0]}: {GREEN}ok{RESET}")
    for module in ("fastapi", "google.adk", "google.antigravity", "sre_common", "sre_agent", "agent"):
        found = importlib.util.find_spec(module) is not None
        print(f"import {module}: {GREEN + 'ok' if found else RED + 'missing - run: uv sync --all-packages'}{RESET}")
        ok &= found
    if os.environ.get("GEMINI_API_KEY"):
        print(f"GEMINI_API_KEY: {GREEN}set{RESET} - the agents will use Gemini")
    else:
        print(f"GEMINI_API_KEY: {YELLOW}not set{RESET} - fine, everything runs in deterministic simulation mode")
    if shutil.which("docker") is None:
        print(f"docker: {YELLOW}not found{RESET} - optional, only needed for the multi-container stack")
    return ok


def run_step(step: int) -> bool:
    title, suites = STEP_TESTS[step]
    print(f"\n== Step {step}: {title}")
    env = {**os.environ}
    # The step tests exercise the deterministic simulation path; a key would switch
    # the Orchestrator to the real SDK and skip them.
    env.pop("GEMINI_API_KEY", None)
    ok = True
    for roots, test_dir, ids in suites:
        paths = [str(REPO_ROOT / root) for root in roots] + [str(REPO_ROOT / test_dir)]
        env["PYTHONPATH"] = os.pathsep.join([*paths, env.get("PYTHONPATH", "")])
        result = subprocess.run(
            [sys.executable, "-m", "unittest", *ids], cwd=REPO_ROOT, env=env, capture_output=True, text=True
        )
        if result.returncode != 0:
            ok = False
            print(result.stderr[-3000:])
    print(f"{GREEN}✅ Step {step} passes{RESET}" if ok else f"{RED}❌ Step {step} is not done yet{RESET}")
    return ok


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    target = argv[0]
    if target == "0":
        return 0 if check_environment() else 1
    steps = sorted(STEP_TESTS) if target == "all" else [int(target)]
    results = [run_step(step) for step in steps]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
