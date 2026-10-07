"""Checks that the workshop is ready to run (for facilitators): `uv run workshop/preflight.py`.

Run it on the branch with the finished code (master), the day before the workshop.

    uv run workshop/preflight.py                      # all checks
    uv run workshop/preflight.py --fast               # without the step ladder (about 1 minute faster)
    uv run workshop/preflight.py --demo https://...   # also check the deployed chat

The checks:
  1. The solution patches and the local `workshop` branch match the code.
  2. The step ladder: at the tag step-0N, steps 1 to N pass and the others fail.
  3. GitHub has the same `workshop` branch and step tags (attendees clone them).
  4. The public URLs work: the installers, and the website.
  5. With --demo: the deployed chat answers.
"""

import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import check

REPO_ROOT = check.REPO_ROOT
GREEN, RED, YELLOW, RESET = check.GREEN, check.RED, check.YELLOW, check.RESET
RAW = "https://raw.githubusercontent.com/xSAVIKx/sre-agent/master"
SITE = "https://xsavikx.github.io/sre-agent/"
STEPS = sorted(check.STEP_TESTS)
TAGS = [f"step-{n:02d}" for n in [0, *STEPS]]

results: list[bool] = []


def report(ok: bool, name: str, detail: str = "") -> None:
    results.append(ok)
    mark = f"{GREEN}✅" if ok else f"{RED}❌"
    print(f"{mark} {name}{RESET}" + (f" - {detail}" if detail else ""))


def git(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8")


def check_generated() -> None:
    subprocess.run([sys.executable, "workshop/build_steps.py"], cwd=REPO_ROOT, capture_output=True)
    stale = git("status", "--porcelain", "--", "workshop/steps").stdout.strip()
    report(not stale, "Solution patches match the code", "run: uv run python workshop/build_steps.py" if stale else "")
    same = git("diff", "--quiet", "HEAD", "refs/heads/workshop").returncode == 0
    detail = "" if same else "run: uv run python workshop/build_steps.py --branch"
    report(same, "The local workshop branch ends with this code", detail)


def check_ladder() -> None:
    """Runs all step checks at each tag, in a temporary worktree."""
    worktree = Path(tempfile.mkdtemp(prefix="workshop-ladder-"))
    git("worktree", "add", "--detach", str(worktree), TAGS[0])
    try:
        for solved, tag in enumerate(TAGS):
            git("checkout", "-q", "-f", tag, cwd=worktree)
            env_python = [sys.executable, str(worktree / "workshop" / "check.py"), "all"]
            output = subprocess.run(env_python, cwd=worktree, capture_output=True, encoding="utf-8").stdout
            passed = [step for step in STEPS if f"Step {step} passes" in output]
            expected = STEPS[:solved]
            report(
                passed == expected, f"Ladder at {tag}", f"passing: {passed or 'none'}, expected: {expected or 'none'}"
            )
    finally:
        git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(worktree, ignore_errors=True)


def check_remote() -> None:
    remote = dict(
        reversed(line.split("\t"))
        for line in git("ls-remote", "origin", "refs/heads/workshop", "refs/tags/step-*").stdout.splitlines()
    )
    local_branch = git("rev-parse", "refs/heads/workshop").stdout.strip()
    pushed = remote.get("refs/heads/workshop") == local_branch
    push = "git push --force-with-lease origin refs/heads/workshop:refs/heads/workshop " + " ".join(TAGS)
    report(pushed, "GitHub has the local workshop branch", "" if pushed else f"push it: {push}")
    tags = [t for t in TAGS if remote.get(f"refs/tags/{t}") != git("rev-parse", t).stdout.strip()]
    report(not tags, "GitHub has the step tags", f"missing or different: {', '.join(tags)}" if tags else "")


def check_url(name: str, url: str, must_contain: str = "") -> None:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = response.read().decode("utf-8", "replace")
        ok = not must_contain or must_contain in body
        report(ok, name, url if ok else f"{url} does not contain {must_contain!r}")
    except Exception as error:  # any failure means the same: attendees cannot use it
        report(False, name, f"{url}: {error}")


def main(argv: list[str]) -> int:
    print("== The code and the generated steps")
    check_generated()
    if "--fast" not in argv:
        print("\n== The step ladder")
        check_ladder()
    print("\n== GitHub")
    check_remote()
    print("\n== Public URLs")
    check_url("The installer for macOS and Linux", f"{RAW}/install.sh", "--workshop")
    check_url("The installer for Windows", f"{RAW}/install.ps1", "Workshop")
    check_url("The workshop website", SITE, "Build an SRE Agent")
    if "--demo" in argv:
        demo = argv[argv.index("--demo") + 1].rstrip("/")
        print("\n== The demo deployment")
        check_url("The demo chat", f"{demo}/chat", "SRE")
        check_url("The demo health check", f"{demo}/health")
    failed = results.count(False)
    print(
        f"\n{GREEN}All {len(results)} checks pass.{RESET}" if not failed else f"\n{RED}{failed} check(s) failed.{RESET}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
