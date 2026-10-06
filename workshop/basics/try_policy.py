"""Shows what the Orchestrator's policy allows: `uv run workshop/basics/try_policy.py [TOOL ...]`.

The Orchestrator (agent/src/agent/config.py) is an Antigravity agent. Before each tool
call, the Antigravity runtime checks the policies from `build_safety_policies()`. This
script asks the same question for some tools, with the same precedence rules as the SDK:

    a rule for the tool beats the wildcard "*", and deny > ask_user > allow.

Give tool names to check other tools.
"""

import os
import sys

# The simulation's policy check follows the SDK's rules and needs no API key.
os.environ.pop("GEMINI_API_KEY", None)
sys.stdout.reconfigure(encoding="utf-8")

from agent.config import build_safety_policies, evaluate_mock_policy  # noqa: E402

# The SRE tools, and some built-in tools of an Antigravity agent.
TOOLS = ["list_incidents", "diagnose_sre", "write_post_mortem", "run_command", "view_file", "read_url_content"]
ICONS = {"allow": "✅ allow", "deny": "⛔ deny", "ask_user": "🙋 ask_user"}


def main(tools: list[str]) -> None:
    policies = build_safety_policies()
    print("The policy:", ", ".join(f'{p.decision}("{p.tool}")' for p in policies), "\n")
    for tool in tools or TOOLS:
        print(f"  {tool:<20} {ICONS[evaluate_mock_policy(policies, tool)]}")


if __name__ == "__main__":
    main(sys.argv[1:])
