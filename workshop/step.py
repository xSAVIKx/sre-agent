"""Moves you through the workshop: `uv run workshop/step.py <command>`.

Commands:
    status          Shows which steps pass and which step is next. Add --json for tools.
    task [N]        Shows the task of step N (default: the next step) and where its TODOs are.
    hint [N]        Shows what the tests of step N expect, from their failure messages.
    solution [N]    Shows the solution of step N. It changes nothing.
    solve [N]       Applies the solution of step N to your code.
    goto N          Jumps to the start of step N (steps 1 to N-1 solved), on a new branch.
                    It first commits your changes, so you lose nothing. `goto 6` gives the
                    finished project.

The steps are git tags: `step-00` has all TODOs open, `step-0N` has steps 1 to N solved.
"""

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import check

REPO_ROOT = check.REPO_ROOT
STEPS_DIR = REPO_ROOT / "workshop" / "steps"
STEPS = check.STEP_TESTS
LAST_STEP = max(STEPS)
WRAP_UP = LAST_STEP + 1
GREEN, RED, YELLOW, RESET = check.GREEN, check.RED, check.YELLOW, check.RESET


class WorkshopError(Exception):
    """A problem that the user can fix. The message tells them how."""


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise WorkshopError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def tag(solved: int) -> str:
    """The tag where steps 1 to `solved` are solved."""
    return f"step-{solved:02d}"


def step_dir(step: int) -> Path:
    matches = sorted(STEPS_DIR.glob(f"{step:02d}-*"))
    if not matches:
        raise WorkshopError(f"There is no step {step}. The steps are 1 to {LAST_STEP}.")
    return matches[0]


def require_tags() -> None:
    if tag(0) not in git("tag", "-l", "step-*").split():
        raise WorkshopError("The workshop steps are missing. Run: git fetch origin --tags")


def uncommitted() -> list[str]:
    return git("status", "--porcelain").splitlines()


# --- status -----------------------------------------------------------------------


def progress() -> dict[int, bool]:
    """Runs the tests of all steps, in parallel. Returns {step: passes}."""
    with ThreadPoolExecutor() as pool:
        return dict(zip(STEPS, pool.map(lambda step: check.test_step(step)[0], STEPS), strict=True))


def next_step(passes: dict[int, bool]) -> int | None:
    return next((step for step, ok in passes.items() if not ok), None)


def status(as_json: bool) -> int:
    passes = progress()
    current = next_step(passes)
    branch = git("branch", "--show-current") or "(no branch)"
    changes = uncommitted()
    if as_json:
        info = {
            "branch": branch,
            "uncommitted_files": len(changes),
            "steps": [{"step": s, "title": STEPS[s][0], "passes": passes[s]} for s in STEPS],
            "next_step": current,
            "next_readme": str(step_dir(current or WRAP_UP).relative_to(REPO_ROOT) / "README.md"),
        }
        print(json.dumps(info, indent=2))
        return 0
    print(f"Branch: {branch}" + (f" ({len(changes)} files not committed)" if changes else ""))
    for step, ok in passes.items():
        mark = f"{GREEN}✅" if ok else f"{RED}❌"
        print(f"  {mark} Step {step}: {STEPS[step][0]}{RESET}")
    if current is None:
        print(f"\n🎉 All steps pass. Read {readme(WRAP_UP)}")
    else:
        later = [s for s in STEPS if s > current and passes[s]]
        print(f"\nNext: step {current}. Read {readme(current)}")
        print(f"  uv run workshop/step.py task {current}   - what to do")
        print(f"  uv run workshop/step.py hint {current}   - what the tests expect")
        print(f"  uv run workshop/step.py solve {current}  - apply the solution, if you are stuck")
        if later:
            print(f"  (Steps {', '.join(map(str, later))} already pass: the steps are independent.)")
    return 0


def readme(step: int) -> str:
    return str((step_dir(step) / "README.md").relative_to(REPO_ROOT))


# --- task, hint, solution ------------------------------------------------------------


def resolve(arg: str | None) -> int:
    """The step number from the command line, or the next step that does not pass."""
    if arg is None:
        step = next_step(progress())
        if step is None:
            raise WorkshopError("All steps pass. There is nothing left to solve.")
        return step
    if not arg.isdigit() or int(arg) not in STEPS:
        raise WorkshopError(f"Give a step from 1 to {LAST_STEP}, not {arg!r}.")
    return int(arg)


def section(markdown: str, heading: str) -> str:
    """The text under a `## heading`, up to the next `## ` heading."""
    match = re.search(rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    return match.group(1).strip() if match else ""


def todos(step: int) -> str:
    result = subprocess.run(
        ["git", "grep", "-n", f"TODO(step-{step})", "--", ":!workshop", ":!skills"],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def task(step: int) -> int:
    text = (step_dir(step) / "README.md").read_text(encoding="utf-8")
    print(f"Step {step}: {STEPS[step][0]}  ({readme(step)})\n")
    print(section(text, "Your task") or text)
    found = todos(step)
    print("\nTODOs in your code:\n" + (found or "  none - you removed them, or the step is solved"))
    return 0


def hint(step: int) -> int:
    ok, failures = check.test_step(step)
    if ok:
        print(f"{GREEN}✅ Step {step} passes. There is nothing to fix.{RESET}")
        return 0
    # The assertion lines say what the tests expected, without the test code around them.
    lines = [line for line in failures.splitlines() if re.match(r"\s*(AssertionError|\w+Error|FAIL:|ERROR:)", line)]
    print(f"Step {step} does not pass yet. The tests say:\n")
    print("\n".join(dict.fromkeys(lines)) or failures)
    found = todos(step)
    if found:
        print(f"\nThe code to change:\n{found}")
    print(f"\nRead the task again: uv run workshop/step.py task {step}")
    return 0


def solution_patch(step: int) -> Path:
    return step_dir(step) / "solution.patch"


def solution(step: int) -> int:
    print(solution_patch(step).read_text(encoding="utf-8"))
    return 0


def solve(step: int) -> int:
    if check.test_step(step)[0]:
        print(f"{GREEN}✅ Step {step} already passes.{RESET}")
        return 0
    patch = str(solution_patch(step))
    try:
        git("apply", "--check", patch)
    except WorkshopError:
        raise WorkshopError(
            f"The solution does not fit your changes to the code of step {step}.\n"
            f"Undo them (git diff shows them), or jump to the next step: uv run workshop/step.py goto {step + 1}"
        ) from None
    git("apply", patch)
    ok = check.test_step(step)[0]
    print(f"{GREEN}✅ Applied the solution of step {step}.{RESET}" if ok else "Applied the solution.")
    print(f"See the change with: git diff. Next: uv run workshop/step.py task {step + 1}" if step < LAST_STEP else "")
    return 0


# --- goto ---------------------------------------------------------------------------


def goto(arg: str) -> int:
    if not arg.isdigit() or not 1 <= int(arg) <= WRAP_UP:
        raise WorkshopError(f"Give a step from 1 to {WRAP_UP}, not {arg!r}.")
    step = int(arg)
    require_tags()
    branch = git("branch", "--show-current")
    if uncommitted():
        if not branch:
            raise WorkshopError(
                "You have changes that are not on a branch. Run: git switch -c my-save, then try again."
            )
        git("add", "--all")
        git("commit", "--quiet", "--no-verify", "-m", f"workshop: my work before the jump to step {step}")
        print(f"Saved your changes in a commit on the branch {branch}.")
    existing = set(git("branch", "--format=%(refname:short)").split())
    base = "my-finished" if step == WRAP_UP else f"my-step-{step}"
    new = next(name for n in range(1, 100) if (name := base if n == 1 else f"{base}-{n}") not in existing)
    git("switch", "--quiet", "-c", new, tag(step - 1))
    print(f"{GREEN}You are on the new branch {new}: the start of step {step}.{RESET}")
    if step == WRAP_UP:
        print(f"All steps are solved here. Read {readme(WRAP_UP)}")
    else:
        print(f"Read {readme(step)}, or run: uv run workshop/step.py task {step}")
    if branch:
        print(f"Your earlier work is on the branch {branch}. To go back: git switch {branch}")
    return 0


COMMANDS = {"task": task, "hint": hint, "solution": solution, "solve": solve}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if argv else 2
    command, args = argv[0], argv[1:]
    try:
        if command == "status":
            return status("--json" in args)
        if command == "goto" and args:
            return goto(args[0])
        if command in COMMANDS:
            return COMMANDS[command](resolve(args[0] if args else None))
        print(__doc__)
        return 2
    except WorkshopError as error:
        print(f"{RED}{error}{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
