"""Builds the workshop website from the Markdown in this repository.

    uv run scripts/build_docs.py           # build into site/
    uv run scripts/build_docs.py --serve   # preview on http://127.0.0.1:8000

The Markdown in the repository is the only source. This script copies the pages into
build/site-src/ (README.md becomes index.md) and rewrites each relative link:

* to another page of the site: a link between the pages;
* to any other file of the repository (code, a folder): a link to it on GitHub.

Then MkDocs with the Material theme builds the site (mkdocs.yml) in strict mode, so a
broken link fails the build. The versions are pinned below.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "build" / "site-src"
GITHUB = "https://github.com/xSAVIKx/sre-agent"
MKDOCS = ["uvx", "--from", "mkdocs==1.6.1", "--with", "mkdocs-material==9.7.7", "mkdocs"]

# Repository files that are not under workshop/, and their place on the site.
EXTRA_PAGES = {"INSTALL.md": "install.md", "EXERCISES.md": "exercises.md"}
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


def assemble() -> None:
    """Writes the site's Markdown into build/site-src/."""
    shutil.rmtree(SOURCE, ignore_errors=True)
    pages = site_pages()
    for repo_path, site_path in pages.items():
        target = SOURCE / site_path
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown = (REPO_ROOT / repo_path).read_text(encoding="utf-8")
        target.write_text(rewrite_links(markdown, repo_path, pages), encoding="utf-8")


def main(argv: list[str]) -> int:
    assemble()
    command = ["serve"] if "--serve" in argv else ["build", "--strict"]
    return subprocess.run([*MKDOCS, *command, "-f", str(REPO_ROOT / "mkdocs.yml")], cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
