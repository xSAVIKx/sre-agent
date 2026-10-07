"""The copy-paste setup prompt for agents is the same everywhere it is shown."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT = (REPO_ROOT / "workshop" / "setup-prompt.txt").read_text(encoding="utf-8").rstrip("\n")
BLOCK = re.compile(r"<!-- setup-prompt:start[^>]*-->\n```text\n(.*?)\n```\n<!-- setup-prompt:end -->", re.S)


class TestSetupPrompt(unittest.TestCase):
    def test_every_copy_matches_the_source(self) -> None:
        for page in ("README.md", "INSTALL.md", "workshop/README.md"):
            with self.subTest(page):
                copies = BLOCK.findall((REPO_ROOT / page).read_text(encoding="utf-8"))
                self.assertEqual(copies, [PROMPT], f"update the prompt in {page} from workshop/setup-prompt.txt")


if __name__ == "__main__":
    unittest.main()
