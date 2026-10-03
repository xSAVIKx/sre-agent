"""Builds the workshop's step-by-step git history from the finished code (maintainers only).

The finished, working project is the single source of truth. Each workshop step
"un-solves" one small piece of it: STEPS below pairs the solution code with the
`TODO(step-N)` starter that replaces it. From those pairs this script derives:

* ``workshop/steps/NN-*/solution.patch`` - the diff that solves step N, so attendees
  who do not want to use git can catch up with ``git apply``;
* a linear ``workshop`` branch: ``step-00`` has every TODO open, ``step-NN`` has
  steps 1..N solved, and the last step equals the finished code;
* local tags ``step-00`` ... ``step-NN`` on those commits.

Usage (from the repository root, on the branch that holds the finished code):

    uv run python workshop/build_steps.py            # write the patches, then commit them
    uv run python workshop/build_steps.py --branch   # build the `workshop` branch + tags

``--branch`` refuses to run while the patches are stale or uncommitted, so the
branch is always reproducible from a commit. It never touches the working tree or
pushes anything; push with ``git push origin workshop`` and ``git push origin --tags``.

When you change code covered by a step, update its ``solution`` string here too:
the script fails loudly if a solution no longer matches the source exactly once.
"""

import difflib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STEPS_DIR = REPO_ROOT / "workshop" / "steps"
BRANCH = "workshop"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import sync_skill  # noqa: E402


@dataclass(frozen=True)
class Edit:
    path: str
    solution: str
    starter: str


@dataclass(frozen=True)
class Step:
    number: int
    slug: str
    title: str
    edits: tuple[Edit, ...]


STEPS: tuple[Step, ...] = (
    Step(
        1,
        "01-find-the-bottleneck",
        "Find the bottleneck",
        (
            Edit(
                "sre_agent/src/sre_agent/gcp_tools.py",
                solution="""    # Calculate exclusive duration for all spans
    exclusive_durations = {}
    for s in spans:
        span_id = s["spanId"]
        child_ids = children_map[span_id]
        covered_ms = _covered_ms(s, [span_map[cid] for cid in child_ids])
        exclusive_durations[span_id] = max(0, inclusive_durations[span_id] - covered_ms)

    # Find the bottleneck (the span with the highest exclusive duration)
    bottleneck_span_id = max(exclusive_durations, key=exclusive_durations.get)
""",
                starter="""    # TODO(step-1): Calculate the exclusive (self) duration of every span, then pick the bottleneck.
    #   - A span's exclusive time is its inclusive time minus the time its children cover.
    #     `children_map[span_id]` lists a span's child IDs, `span_map[child_id]` gives the child
    #     span, and `_covered_ms(span, children)` returns the ms the children cover (overlaps
    #     counted once). Never let it drop below 0.
    #   - The bottleneck is the span with the largest exclusive duration.
    exclusive_durations = {s["spanId"]: 0 for s in spans}
    bottleneck_span_id = spans[0]["spanId"]
""",
            ),
        ),
    ),
    Step(
        2,
        "02-feed-the-metrics",
        "Feed the metrics",
        (
            Edit(
                "app/main.py",
                solution="""    # During the incident the app is idle-waiting on the database, so CPU stays low
    # while the connection pool sits at its limit.
    cpu = [0.18, 0.21, 0.24] if trigger_error else [0.18, 0.19, 0.17]
    connections = [62, 97, DB_MAX_CONNECTIONS] if trigger_error else [12, 14, 13]
    return [
        {
            "metric": {"type": CPU_METRIC, "labels": {"service_name": "sre-chaos-monkey"}},
            "points": [{"value": v} for v in cpu],
        },
        {
            "metric": {"type": DB_CONNECTIONS_METRIC, "labels": {"database_id": "db-primary"}},
            "points": [{"value": v} for v in connections],
        },
    ]
""",
                starter="""    # TODO(step-2): Return two time series so the SRE agent's `query_metrics` tool finds data.
    #   Each one looks like:
    #     {"metric": {"type": <metric type>, "labels": {<label>: <value>}}, "points": [{"value": v}, ...]}
    #   1. CPU_METRIC with label service_name="sre-chaos-monkey": readings are 0-1 fractions.
    #   2. DB_CONNECTIONS_METRIC with label database_id="db-primary": when `trigger_error` is
    #      True the last reading must be DB_MAX_CONNECTIONS (the pool is exhausted).
    return []
""",
            ),
        ),
    ),
    Step(
        3,
        "03-give-the-agent-tools",
        "Give the agent its tools",
        (
            Edit(
                "sre_agent/src/sre_agent/sre_workflow.py",
                solution="""    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem],
""",
                starter="""    # TODO(step-3): Give the LogCorrelator its toolbelt. Gemini decides when to call them, based
    #   on their type hints and docstrings. They are already imported at the top of this file:
    #   query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem.
    tools=[],
""",
            ),
        ),
    ),
    Step(
        4,
        "04-lock-it-down",
        "Lock the Orchestrator down",
        (
            Edit(
                "agent/src/agent/config.py",
                solution="""    return [deny("*"), allow("diagnose_sre")]
""",
                starter="""    # TODO(step-4): Everything is denied, so the Orchestrator cannot even delegate. Keep the
    #   deny-by-default rule and add one `allow(...)` for the single tool it needs.
    return [deny("*")]
""",
            ),
        ),
    ),
    Step(
        5,
        "05-show-the-severity",
        "Show the severity",
        (
            Edit(
                "agent/src/agent/a2ui_translator.py",
                solution="""    match = re.search(r"\\(([\\d.]+)% of total trace\\)", text)
    if not match:
        return None
    contribution = float(match.group(1))
    level = next(name for threshold, name in SEVERITY_THRESHOLDS if contribution >= threshold)
    return {"type": "severity_badge", "level": level, "contribution": contribution}
""",
                starter="""    # TODO(step-5): Find the "(NN.N% of total trace)" figure in `text` with `re.search`, turn it
    #   into a float, pick the first level in SEVERITY_THRESHOLDS whose threshold it reaches, and
    #   return {"type": "severity_badge", "level": <level>, "contribution": <the float>}.
    #   Return None when the report has no such figure.
    return None
""",
            ),
        ),
    ),
)


def _git(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _read_head(path: str) -> str:
    return _git("show", f"HEAD:{path}") + "\n"


def _state(solved_through: int, head_files: dict[str, str]) -> dict[str, str]:
    """Returns {path: content} for every file that differs from HEAD in this state."""
    files: dict[str, str] = {}
    for step in STEPS:
        if step.number <= solved_through:
            continue
        for edit in step.edits:
            content = files.get(edit.path, head_files[edit.path])
            files[edit.path] = content.replace(edit.solution, edit.starter)
    # The skill mirror is generated from sre_agent/; keep it consistent in every state.
    prefix = "sre_agent/src/sre_agent/"
    for path, content in list(files.items()):
        name = path.removeprefix(prefix)
        if path.startswith(prefix) and name in sync_skill.MODULES:
            mirrored = sync_skill.HEADER.format(name=name) + sync_skill._ABSOLUTE_IMPORT.sub(
                r"\1from .\2 import", content
            )
            files[f"skills/sre_incident_solver/{name}"] = mirrored
    return files


def _patch(before: dict[str, str], after: dict[str, str]) -> str:
    chunks = []
    for path in sorted(set(before) | set(after)):
        old, new = before[path], after[path]
        if old == new:
            continue
        chunks.extend(
            difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}")
        )
    return "".join(chunks)


def _head_files() -> dict[str, str]:
    paths = {edit.path for step in STEPS for edit in step.edits}
    paths |= {f"skills/sre_incident_solver/{p.removeprefix('sre_agent/src/sre_agent/')}" for p in paths}
    files = {}
    for path in paths:
        try:
            files[path] = _read_head(path)
        except subprocess.CalledProcessError:
            continue
    for step in STEPS:
        for edit in step.edits:
            count = files[edit.path].count(edit.solution)
            if count != 1:
                sys.exit(f"step {step.number}: solution found {count}x in {edit.path} at HEAD (expected 1)")
    return files


def write_patches(head_files: dict[str, str]) -> list[Path]:
    """Writes each step's solution.patch; returns the files that changed."""
    changed = []
    for step in STEPS:
        before = {**head_files, **_state(step.number - 1, head_files)}
        after = {**head_files, **_state(step.number, head_files)}
        target = STEPS_DIR / step.slug / "solution.patch"
        target.parent.mkdir(parents=True, exist_ok=True)
        patch = _patch(before, after)
        if not target.exists() or target.read_text(encoding="utf-8") != patch:
            target.write_text(patch, encoding="utf-8")
            changed.append(target)
    return changed


def build_branch(head_files: dict[str, str]) -> None:
    """Creates the linear `workshop` branch and step tags from HEAD, without touching the worktree."""
    head = _git("rev-parse", "HEAD")
    parent = head
    with tempfile.TemporaryDirectory() as tmp:
        index_env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        for solved in range(len(STEPS) + 1):
            _git("read-tree", head, env=index_env)
            for path, content in _state(solved, head_files).items():
                blob = _git("hash-object", "-w", "--stdin", stdin=content)
                mode = _git("ls-files", "-s", "--", path).split()[0]
                _git("update-index", "--cacheinfo", f"{mode},{blob},{path}", env=index_env)
            tree = _git("write-tree", env=index_env)
            if solved == 0:
                message = "workshop: step-00 - starting point, every TODO(step-N) open"
            else:
                step = STEPS[solved - 1]
                message = f"workshop: step-{solved:02d} - {step.title} (solution)"
            parent = _git("commit-tree", tree, "-p", parent, "-m", message)
            _git("tag", "-f", f"step-{solved:02d}", parent)
            print(f"step-{solved:02d} -> {parent[:10]}  {message}")
    _git("branch", "-f", BRANCH, parent)
    print(f"Branch '{BRANCH}' -> {parent[:10]}. The last step matches HEAD's code.")


def main(argv: list[str]) -> int:
    head_files = _head_files()
    changed = write_patches(head_files)
    for path in changed:
        print(f"updated {path.relative_to(REPO_ROOT)}")
    if "--branch" not in argv:
        if changed:
            print("Commit the updated patches, then run again with --branch.")
        return 0
    if changed or _git("status", "--porcelain", "--", "workshop"):
        sys.exit("workshop/ has uncommitted changes; commit them first so the branch is reproducible.")
    build_branch(head_files)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
