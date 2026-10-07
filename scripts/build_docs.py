"""Builds the workshop website from the Markdown in this repository.

    uv run scripts/build_docs.py           # build into site/
    uv run scripts/build_docs.py --serve   # preview on http://127.0.0.1:8000

The Markdown in the repository is the only source. This script copies the pages into
build/site-src/ (README.md becomes index.md) and rewrites each relative link:

* to another page of the site: a link between the pages;
* to any other file of the repository (code, a folder): a link to it on GitHub.

Each step page also gets a collapsed hint and solution at its end. They come from the
step definitions in workshop/build_steps.py and the committed solution.patch, so they
always agree with the code.

Then MkDocs with the Material theme builds the site (mkdocs.yml) in strict mode, so a
broken link fails the build. The versions are pinned below.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "workshop"))
import build_steps  # noqa: E402

SOURCE = REPO_ROOT / "build" / "site-src"
GITHUB = "https://github.com/xSAVIKx/sre-agent"
MKDOCS = ["uvx", "--from", "mkdocs==1.6.1", "--with", "mkdocs-material==9.7.7", "mkdocs"]

# Repository files that are not under workshop/, and their place on the site.
EXTRA_PAGES = {"INSTALL.md": "install.md", "EXERCISES.md": "exercises.md"}
# Text blocks (the setup prompt, log output) wrap, so readers see them whole before they copy.
EXTRA_CSS = ".md-typeset .language-text :is(pre, code) { white-space: pre-wrap; overflow-wrap: anywhere; }\n"
ITEM = re.compile(r"(\s*)([*-]|\d+\.)\s+\S")
LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")


def site_pages() -> dict[str, str]:
    """{repository path: site path} for each page of the site."""
    pages = dict(EXTRA_PAGES)
    for path in sorted((REPO_ROOT / "workshop").rglob("*.md")):
        repo_path = path.relative_to(REPO_ROOT).as_posix()
        site_path = repo_path.removeprefix("workshop/")
        pages[repo_path] = re.sub(r"(^|/)README\.md$", r"\1index.md", site_path)
    return pages


def rewrite_links(markdown: str, repo_path: str, pages: dict[str, str]) -> str:
    """The page's relative links, pointed at the site's pages or at GitHub."""
    page_dir = PurePosixPath(pages[repo_path]).parent

    def rewrite(match: re.Match[str]) -> str:
        text, target = match.groups()
        if re.match(r"[a-z]+:|#|/", target):  # a URL, an anchor on this page, or an absolute path
            return match.group(0)
        path, _, anchor = target.partition("#")
        resolved = _normalize(PurePosixPath(repo_path).parent / path)
        anchor = f"#{anchor}" if anchor else ""
        if resolved in pages:
            relative = _relative(PurePosixPath(pages[resolved]), page_dir)
            return f"[{text}]({relative}{anchor})"
        kind = "tree" if (REPO_ROOT / resolved).is_dir() else "blob"
        return f"[{text}]({GITHUB}/{kind}/master/{resolved}{anchor})"

    return LINK.sub(rewrite, markdown)


def normalize_lists(markdown: str) -> str:
    """Indents the content of list items by 4 spaces, as Python-Markdown needs.

    The READMEs indent it to the item's text (3 spaces after "1. "), which GitHub accepts.
    Python-Markdown then ends the list at a nested code block or list, and the numbering
    starts again at 1.
    """
    out: list[str] = []
    stack: list[tuple[int, int]] = []  # (original content indent, new content indent) of open items
    fence: str | None = None
    for line in markdown.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if fence is None and stripped:
            while stack and indent < stack[-1][0]:
                stack.pop()
        new_indent = stack[-1][1] + indent - stack[-1][0] if stack and stripped else indent
        if fence is None:
            item = ITEM.match(line)
            if item and stripped:
                marker = len(item.group(2)) + 1
                stack.append((indent + marker, new_indent + 4))
            if stripped.startswith("```"):
                fence = stripped[:3]
        elif stripped.startswith(fence):
            fence = None
        out.append(" " * new_indent + line[indent:] if stripped else "")
    return "\n".join(out) + "\n"


def _normalize(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    return "/".join(parts)


def _relative(target: PurePosixPath, start: PurePosixPath) -> str:
    common = 0
    while common < min(len(target.parts), len(start.parts)) and target.parts[common] == start.parts[common]:
        common += 1
    return "/".join([".."] * (len(start.parts) - common) + list(target.parts[common:]))


def _enclosing(path: str, solution: str) -> str:
    """Where the solution is: in its function or class, or at the module level."""
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    before = source[: source.index(solution)]
    names = re.findall(r"(?m)^(?:async def|def|class) (\w+)", before)
    module_level = not solution[:1].isspace()
    return "at the module level" if module_level or not names else f"in `{names[-1]}()`"


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" if line else "" for line in text.splitlines())


def _solution_diff(step: "build_steps.Step") -> str:
    """The step's solution.patch, without the generated skill mirror."""
    patch = (build_steps.STEPS_DIR / step.slug / "solution.patch").read_text(encoding="utf-8")
    files = re.split(r"(?m)^(?=--- a/)", patch)
    return "".join(f for f in files if f.strip() and not f.startswith("--- a/.agents/")).rstrip()


def spoilers(step: "build_steps.Step") -> str:
    """The collapsed "Hint" and "Solution" blocks at the end of a step page."""
    hints = []
    for edit in step.edits:
        comment = "\n".join(line.strip() for line in edit.starter.splitlines() if line.strip().startswith("#"))
        hints.append(
            f"**File:** `{edit.path}`, {_enclosing(edit.path, edit.solution)}. The TODO says:\n\n"
            f"```text\n{comment}\n```"
        )
    hint = "\n\n".join(hints)
    solution = (
        f"```diff\n{_solution_diff(step)}\n```\n\n"
        f"To apply it to your code: `uv run workshop/step.py solve {step.number}`."
    )
    return (
        "\n\n## Hint and solution\n\n"
        "Try the step first. Open the hint when you are stuck, and the solution only after the hint.\n\n"
        f'??? tip "Hint: where to change the code"\n\n{_indent(hint)}\n\n'
        f'??? success "Solution"\n\n{_indent(solution)}\n'
    )


def assemble() -> None:
    """Writes the site's Markdown into build/site-src/."""
    shutil.rmtree(SOURCE, ignore_errors=True)
    pages = site_pages()
    for repo_path, site_path in pages.items():
        target = SOURCE / site_path
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown = (REPO_ROOT / repo_path).read_text(encoding="utf-8")
        markdown = normalize_lists(rewrite_links(markdown, repo_path, pages))
        step = next((st for st in build_steps.STEPS if repo_path == f"workshop/steps/{st.slug}/README.md"), None)
        if step:
            markdown = markdown.rstrip() + spoilers(step)
        target.write_text(markdown, encoding="utf-8")
    (SOURCE / "extra.css").write_text(EXTRA_CSS, encoding="utf-8")
    (SOURCE / "llms.txt").write_text(llms_txt(), encoding="utf-8")


def llms_txt() -> str:
    """/llms.txt: what an agent needs to set up the workshop, and where to read more."""
    prompt = (REPO_ROOT / "workshop" / "setup-prompt.txt").read_text(encoding="utf-8").rstrip()
    links = {
        "Install, with problems and fixes": "install/",
        "Basics of ADK, A2A, A2UI and the Antigravity SDK": "basics/",
        "Step 0, setup and tour": "steps/00-setup/",
    }
    site = "https://xsavikx.github.io/sre-agent/"
    return (
        "# Workshop: Build an SRE Agent\n\n"
        "> A 90-minute workshop: build an SRE agent with ADK, A2A, A2UI and the Antigravity SDK.\n\n"
        "## Instructions for an agent that sets up the workshop\n\n"
        f"{prompt}\n\n"
        "## Pages\n\n" + "".join(f"- [{title}]({site}{path})\n" for title, path in links.items())
    )


def main(argv: list[str]) -> int:
    assemble()
    command = ["serve"] if "--serve" in argv else ["build", "--strict"]
    return subprocess.run([*MKDOCS, *command, "-f", str(REPO_ROOT / "mkdocs.yml")], cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
