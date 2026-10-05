"""workshop/step.py: the command that moves participants through the workshop.

The tests run step.py against a small throwaway git repository with its own step
tags and solution patch, so they need neither the real workshop tags (CI clones
without them) nor a git identity (a new laptop often has none).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workshop"))
import step

TODO = "def answer():\n    return 0  # TODO(step-1): return the answer\n"
SOLVED = "def answer():\n    return 42\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


class TestStepCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        # No global or system git config: like a laptop where git has no user.name / user.email.
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.root)}
        self.enterContext(mock.patch.dict(os.environ, env))
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        _git(self.root, "init", "--quiet", "--initial-branch=my-work")
        (self.root / "answer.py").write_text(TODO, encoding="utf-8")
        steps_dir = self.root / "workshop" / "steps"
        (steps_dir / "01-answer").mkdir(parents=True)
        (steps_dir / "01-answer" / "README.md").write_text("# Step 1\n\n## Your task\n\nReturn 42.\n\n## Run it\n")
        (steps_dir / "02-wrap-up").mkdir()
        (steps_dir / "02-wrap-up" / "README.md").write_text("# Done\n")
        _git(self.root, "add", "-A")
        _git(self.root, *ident, "commit", "--quiet", "-m", "start")
        _git(self.root, "tag", "step-00")
        (self.root / "answer.py").write_text(SOLVED, encoding="utf-8")
        patch = _git(self.root, "diff") + "\n"
        (steps_dir / "01-answer" / "solution.patch").write_text(patch, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, *ident, "commit", "--quiet", "-m", "solved")
        _git(self.root, "tag", "step-01")
        _git(self.root, "checkout", "--quiet", "step-00", "--", "answer.py")
        _git(self.root, *ident, "commit", "--quiet", "-am", "back to the start")

        def test_step(number: int) -> tuple[bool, str]:
            ok = "42" in (self.root / "answer.py").read_text(encoding="utf-8")
            return ok, "" if ok else "AssertionError: 0 != 42"

        for name, value in {
            "REPO_ROOT": self.root,
            "STEPS_DIR": steps_dir,
            "STEPS": {1: ("Return the answer", [])},
            "LAST_STEP": 1,
            "WRAP_UP": 2,
        }.items():
            self.enterContext(mock.patch.object(step, name, value))
        self.enterContext(mock.patch.object(step.check, "test_step", test_step))

    def test_status_names_the_next_step(self) -> None:
        self.assertEqual(step.next_step(step.progress()), 1)

    def test_section_reads_one_heading(self) -> None:
        self.assertEqual(step.section("## Your task\n\nDo it.\n\n## Run it\nx", "Your task"), "Do it.")

    def test_solve_applies_the_patch(self) -> None:
        self.assertEqual(step.solve(1), 0)
        self.assertEqual((self.root / "answer.py").read_text(encoding="utf-8"), SOLVED)

    def test_solve_does_not_overwrite_own_changes(self) -> None:
        (self.root / "answer.py").write_text("def answer():\n    return 41  # my attempt\n", encoding="utf-8")
        with self.assertRaisesRegex(step.WorkshopError, "goto 2"):
            step.solve(1)
        self.assertIn("my attempt", (self.root / "answer.py").read_text(encoding="utf-8"))

    def test_goto_saves_the_work_and_starts_a_branch(self) -> None:
        (self.root / "answer.py").write_text("def answer():\n    return 41  # my attempt\n", encoding="utf-8")
        self.assertEqual(step.goto("2"), 0)
        self.assertEqual(_git(self.root, "branch", "--show-current"), "my-finished")
        self.assertEqual((self.root / "answer.py").read_text(encoding="utf-8"), SOLVED)
        # The attempt is committed on the old branch, even without a git identity.
        self.assertIn("my attempt", _git(self.root, "show", "my-work:answer.py"))

    def test_goto_never_reuses_a_branch(self) -> None:
        step.goto("1")
        _git(self.root, "switch", "--quiet", "my-work")
        step.goto("1")
        self.assertEqual(_git(self.root, "branch", "--show-current"), "my-step-1-2")

    def test_unknown_step_is_a_clear_error(self) -> None:
        with self.assertRaisesRegex(step.WorkshopError, "from 1 to 2"):
            step.goto("9")
        with self.assertRaisesRegex(step.WorkshopError, "from 1 to 1"):
            step.resolve("x")


if __name__ == "__main__":
    unittest.main()
