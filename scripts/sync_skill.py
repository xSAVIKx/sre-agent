"""Regenerates the portable Antigravity skill from the `sre_agent` package.

`skills/sre_incident_solver/` is a mirror of the diagnostics engine that the
Antigravity CLI and desktop app can load without the rest of the workspace. It
used to be maintained by hand and drifted far behind the engine. This script
copies the engine modules into the skill, rewriting `sre_agent.` absolute
imports into package-relative ones, so the mirror can always be regenerated
instead of edited.

Usage:
    uv run python scripts/sync_skill.py          # rewrite the mirror
    uv run python scripts/sync_skill.py --check  # exit 1 if the mirror is stale
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "sre_agent" / "src" / "sre_agent"
SKILL_DIR = REPO_ROOT / "skills" / "sre_incident_solver"

# The engine modules the workflow needs. The service-only modules (main.py,
# routes.py) are deliberately left out: the skill has no HTTP server.
MODULES = (
    "config.py",
    "firestore_strategy.py",
    "gcp_tools.py",
    "itinerary.py",
    "registry.py",
    "sre_workflow.py",
)

HEADER = (
    "# GENERATED from sre_agent/src/sre_agent/{name} by scripts/sync_skill.py - do not edit.\n"
    "# Change the engine module instead, then run: uv run python scripts/sync_skill.py\n"
)

_ABSOLUTE_IMPORT = re.compile(r"^(\s*)from sre_agent\.(\w+) import", re.MULTILINE)


def render(name: str) -> str:
    """Returns the skill copy of one engine module."""
    source = (SOURCE_DIR / name).read_text(encoding="utf-8")
    return HEADER.format(name=name) + _ABSOLUTE_IMPORT.sub(r"\1from .\2 import", source)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for name in MODULES:
        expected = render(name)
        target = SKILL_DIR / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == expected:
            continue
        stale.append(name)
        if not check:
            target.write_text(expected, encoding="utf-8")

    if check and stale:
        print(f"Skill mirror is stale: {', '.join(stale)}. Run: uv run python scripts/sync_skill.py")
        return 1
    if stale and not check:
        print(f"Regenerated: {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
