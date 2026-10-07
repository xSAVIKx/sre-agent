"""The website's hint and solution for each workshop step agree with the step itself."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_docs  # noqa: E402


class TestStepSpoilers(unittest.TestCase):
    def test_each_step_shows_its_todo_and_its_patch(self) -> None:
        for step in build_docs.build_steps.STEPS:
            with self.subTest(step.slug):
                spoilers = build_docs.spoilers(step)
                patch = (REPO_ROOT / "workshop" / "steps" / step.slug / "solution.patch").read_text(encoding="utf-8")
                for edit in step.edits:
                    self.assertIn(f"`{edit.path}`", spoilers)
                    self.assertIn(f"+++ b/{edit.path}", patch)
                # Every changed line of the attendee's files is in the solution; the mirror is not.
                changed = [line for line in patch.splitlines() if line[:1] in "+-" and ".agents/" not in line]
                own = [line for line in changed if not line.startswith(("+++", "---"))]
                self.assertTrue(own)
                self.assertTrue(all(f"    {line}" in spoilers for line in own if line.strip("+-").strip()))
                self.assertNotIn(".agents/skills/", spoilers)
                self.assertIn(f"uv run workshop/step.py solve {step.number}", spoilers)


if __name__ == "__main__":
    unittest.main()
