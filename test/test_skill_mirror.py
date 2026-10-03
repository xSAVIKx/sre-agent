"""The portable skill must be a faithful, generated copy of the SRE engine."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_skill  # noqa: E402


class TestSkillMirror(unittest.TestCase):
    def test_mirror_matches_engine(self) -> None:
        """Fails when `sre_agent` changed without regenerating the skill copy."""
        self.assertEqual(sync_skill.main(["--check"]), 0, "run: uv run python scripts/sync_skill.py")

    def test_skill_manifest_has_frontmatter(self) -> None:
        """Skill loaders read `name` and `description` from YAML frontmatter."""
        text = (REPO_ROOT / "skills" / "sre_incident_solver" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n")[1]
        self.assertIn("name: sre_incident_solver", frontmatter)
        self.assertIn("description:", frontmatter)


if __name__ == "__main__":
    unittest.main()
