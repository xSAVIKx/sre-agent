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
        "01-wire-the-workflow",
        "Connect the agents in a workflow",
        (
            Edit(
                "sre_agent/src/sre_agent/sre_workflow.py",
                solution="""        sre_diagnostics_workflow = AdkWorkflow(
            name="sre_diagnostics_workflow", edges=[(START, trace_analyzer, fetch_telemetry, log_correlator)]
        )
""",
                starter="""        # TODO(step-1): The workflow stops after the TraceAnalyzer. Make it a chain of 4 nodes:
        #   START -> trace_analyzer -> fetch_telemetry -> log_correlator.
        #   A tuple in `edges` is a chain: each node gets the output of the node before it. Thus
        #   fetch_telemetry gets the trace ID, and log_correlator gets the spans and logs.
        sre_diagnostics_workflow = AdkWorkflow(name="sre_diagnostics_workflow", edges=[(START, trace_analyzer)])
""",
            ),
        ),
    ),
    Step(
        2,
        "02-build-the-agent",
        "Give the agent its tools",
        (
            Edit(
                "sre_agent/src/sre_agent/sre_workflow.py",
                solution="""log_correlator = AdkAgent(
    name="log_correlator",
    model=MODEL,
    instruction=LOG_CORRELATOR_INSTRUCTION,
    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem],
)
""",
                starter="""# TODO(step-2): Build the LogCorrelator as an ADK agent: AdkAgent(name=..., model=..., ...).
#   - name: "log_correlator". The workflow and the logs use this name.
#   - model: MODEL. It is Gemini when GEMINI_API_KEY is set, and a scripted model if not.
#   - instruction: LOG_CORRELATOR_INSTRUCTION (above). It tells the model its job.
#   - tools: the four functions imported at the top of this file: query_metrics,
#     list_metric_descriptors, analyze_trace_cascade, generate_post_mortem. ADK reads their
#     type hints and docstrings, and tells the model how to call them.
log_correlator = AdkAgent(name="log_correlator", model=MODEL, instruction="Describe the trace.")
""",
            ),
        ),
    ),
    Step(
        3,
        "03-publish-a-skill",
        "Publish an A2A skill",
        (
            Edit(
                "sre_agent/src/sre_agent/a2a_agent.py",
                solution="""    if skill == WRITE_POST_MORTEM:
        return run_post_mortem(prompt=prompt, project_id=project_id, trace_id=trace_id, ui=ui)
""",
                starter="""    # TODO(step-3): Run the WRITE_POST_MORTEM skill: return
    #   run_post_mortem(prompt=prompt, project_id=project_id, trace_id=trace_id, ui=ui).
""",
            ),
            Edit(
                "sre_agent/src/sre_agent/a2a_agent.py",
                solution="""            AgentSkill(
                id=WRITE_POST_MORTEM,
                name="Write a post-mortem",
                description=(
                    "Writes the incident post-mortem of one trace from its spans and logs: overview, timeline, "
                    "root cause and next steps. With a Gemini key, adds AI-generated analyst notes. Request "
                    'metadata: skill="write_post_mortem", project_id, trace_id (optional: defaults to the most '
                    "important recent incident)."
                ),
                tags=["sre", "post-mortem"],
                examples=["Write the post-mortem for trace 1c65bf87e4be434ea6d6d7edc1ef8c97."],
                output_modes=["text/markdown", "application/json"],
            ),
""",
                starter="""            # TODO(step-3): Publish the post-mortem skill: an AgentSkill with id=WRITE_POST_MORTEM,
            #   a name, a description (what it does, and its request metadata: skill, project_id,
            #   trace_id), tags, examples and output_modes. Use the two skills above as examples.
""",
            ),
        ),
    ),
    Step(
        4,
        "04-call-it-safely",
        "Call the agent over A2A, safely",
        (
            Edit(
                "agent/src/agent/config.py",
                solution="""    if sink is not None:  # a chat: its UI renders A2UI
        metadata["a2uiClientCapabilities"] = A2UI_CLIENT_CAPABILITIES
    try:
        result = await call_agent(
            base_url,
            prompt,
            metadata,
            context_id=sink.context_id if sink else "",
            on_progress=_emit_progress,
            extensions=[A2UI_EXTENSION_URI] if sink is not None else None,
        )
        report = result.text
        if sink is not None:
            sink.a2ui = result.a2ui
""",
                starter="""    try:
        # TODO(step-4): Send the request to the SRE agent over A2A, and keep its answer:
        #   1. For a chat (`sink is not None`), add A2UI_CLIENT_CAPABILITIES to the metadata, under
        #      the key "a2uiClientCapabilities". Then the SRE agent also sends A2UI surfaces.
        #   2. result = await call_agent(base_url, prompt, metadata, context_id=..., on_progress=...,
        #      extensions=...). Use sink.context_id (or "" without a sink), _emit_progress, and
        #      [A2UI_EXTENSION_URI] for a chat (or None).
        #   3. report = result.text. For a chat, also keep the surface: sink.a2ui = result.a2ui.
        raise NotImplementedError("TODO(step-4): call the SRE agent over A2A")
""",
            ),
            Edit(
                "agent/src/agent/config.py",
                solution="""    return [deny("*"), allow("list_incidents"), allow("diagnose_sre"), allow("write_post_mortem")]
""",
                starter="""    # TODO(step-4): Everything is denied, so the Orchestrator cannot even delegate. Keep the
    #   deny-by-default rule and add one `allow(...)` per tool it needs: one per SRE agent skill.
    return [deny("*")]
""",
            ),
        ),
    ),
    Step(
        5,
        "05-show-the-severity",
        "Send UI with A2UI",
        (
            Edit(
                "sre_agent/src/sre_agent/a2ui_surfaces.py",
                solution="""            "action": {"event": {"name": action, "context": {"traceId": trace_id}}},
""",
                starter="""            # TODO(step-5): The click must tell the agent which trace: add a "context" with
            #   {"traceId": trace_id} to the event. In a list row, trace_id is a data binding
            #   ({"path": "traceId"}): the browser puts in the trace ID of that row.
            "action": {"event": {"name": action}},
""",
            ),
            Edit(
                "sre_agent/src/sre_agent/a2ui_surfaces.py",
                solution="""    if bottleneck_share is None:
        return []
    level = classify_severity(bottleneck_share)
    return [{"id": "severity", "component": "SeverityBadge", "level": level, "contribution": bottleneck_share}]
""",
                starter="""    # TODO(step-5): Without a bottleneck there is no badge: return []. Otherwise return one
    #   component: {"id": "severity", "component": "SeverityBadge", "level": <classify_severity>,
    #   "contribution": <the share>}. The cards list it first, and the browser draws it.
    return []
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
            files[f".agents/skills/sre_incident_solver/{name}"] = mirrored
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
    paths |= {f".agents/skills/sre_incident_solver/{p.removeprefix('sre_agent/src/sre_agent/')}" for p in paths}
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
