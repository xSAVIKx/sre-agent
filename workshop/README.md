# Workshop: Build an SRE Agent (90 minutes)

In this workshop, you complete an SRE agent that does not work fully yet. You do these tasks:

1. Make the SRE agent find the slowest span in a distributed trace.
2. Give the SRE agent metrics data.
3. Give the Gemini log correlator agent its tools.
4. Set a deny-by-default safety policy on the Orchestrator, which is the agent that users talk to.
5. Show the incident severity in the chat UI.

All steps run **on your laptop**. You do not need a cloud account or an API key.

A `GEMINI_API_KEY` is optional:

* With a key, the agents use Gemini to find the root cause.
* Without a key, each agent uses a deterministic simulation. The report has the same structure.

## Before the workshop (10 minutes, at home)

You need **git** and a terminal. Do the installation at home: it downloads a few hundred megabytes, and
the wifi at events is often slow.

Run this command. It installs uv (and Python, if necessary), clones the repository, creates your
own branch `my-work` at the tag `step-00`, installs the dependencies and checks your setup:

```bash
# macOS and Linux
curl -LsSf https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.sh | sh -s -- --workshop
```

```powershell
# Windows (PowerShell)
$env:SRE_AGENT_WORKSHOP = "1"
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.ps1 | iex"
```

When all lines of the check say `ok`, you are ready. Yellow lines are optional. If a line is red,
see [Problems and fixes](../INSTALL.md#problems-and-fixes), or open the repository in Antigravity
and ask: "Prepare my computer for the SRE agent workshop."

If you already have the repository and uv:

```bash
git fetch origin --tags
git switch -c my-work step-00      # your own branch, starting at step 0
uv sync --all-packages             # downloads the dependencies
uv run workshop/check.py 0         # checks your setup
```

Optional: set `GEMINI_API_KEY` ([get a key](https://aistudio.google.com/apikey)):
`export GEMINI_API_KEY=...` (macOS, Linux) or `$env:GEMINI_API_KEY = "..."` (PowerShell).

## Agenda

| Time      | Step                                                           | File that you change                      |
|:----------|:---------------------------------------------------------------|:------------------------------------------|
| 0:00      | Introduction and live demo                                     | –                                         |
| 0:10      | [0 · Setup and tour](steps/00-setup/README.md)                 | –                                         |
| 0:20      | [1 · Find the bottleneck](steps/01-find-the-bottleneck/README.md) | `sre_agent/.../gcp_tools.py`           |
| 0:32      | [2 · Feed the metrics](steps/02-feed-the-metrics/README.md)    | `app/main.py`                             |
| 0:44      | [3 · Give the agent its tools](steps/03-give-the-agent-tools/README.md) | `sre_agent/.../sre_workflow.py`  |
| 0:54      | [4 · Lock the Orchestrator down](steps/04-lock-it-down/README.md) | `agent/.../config.py`                  |
| 1:06      | [5 · Show the severity](steps/05-show-the-severity/README.md)  | `sre_agent/.../a2ui_surfaces.py`          |
| 1:20      | [6 · Wrap-up: production and next steps](steps/06-wrap-up/README.md) | –                                   |

Each step has a `# TODO(step-N)` comment in the code. To find the comment for step 1, do this command:

```bash
git grep -n "TODO(step-1)"
```

## How the steps work

Each step is a **git tag**:

* `step-00` is the start point. All TODO comments are open.
* `step-03` has the solutions for steps 1, 2 and 3.
* `step-05` is the completed project.

One command moves you through the workshop. It works on macOS, Linux and Windows:

| Task                                         | Command                              |
|:---------------------------------------------|:-------------------------------------|
| Show your progress and the next step         | `uv run workshop/step.py status`     |
| Show the task of step N and its TODOs        | `uv run workshop/step.py task N`     |
| Show what the tests of step N expect         | `uv run workshop/step.py hint N`     |
| Check your work on step N (full test output) | `uv run workshop/check.py N`         |
| Show the solution of step N                  | `uv run workshop/step.py solution N` |
| Apply the solution of step N (if you are stuck) | `uv run workshop/step.py solve N` |
| Jump to the start of step N                  | `uv run workshop/step.py goto N`     |
| Start again                                  | `uv run workshop/step.py goto 1`     |
| Get the finished project                     | `uv run workshop/step.py goto 6`     |

Without N, `task`, `hint`, `solution` and `solve` use your next step.

`goto` loses nothing: it commits your changes on your current branch, then starts a new branch
(for example `my-step-3`). To go back, use `git switch my-work`.

### Ask an agent for help

Open the repository in [Antigravity](https://antigravity.google), or run `agy` in it. The skill
`sre-workshop-coach` uses the same command. Ask, for example:

* "Where am I in the workshop?"
* "I am stuck on step 2. Give me a hint." (Ask again for a bigger hint.)
* "Check my code for step 1."
* "I am behind. Skip to step 4."

The coach explains and gives hints one level at a time. It changes your code only when you ask it
to solve or skip a step.

The `solution.patch` files also change `skills/sre_incident_solver/`. This directory is a generated
copy of the SRE agent (see [step 6](steps/06-wrap-up/README.md)). Do not edit it.

## The two commands that you use in all steps

```bash
# Generate an incident and run the diagnosis in your terminal:
uv run simulate_incident.py                 # through the Orchestrator agent (the real path)
uv run simulate_incident.py --engine-only   # straight to the SRE engine (skips the Orchestrator)

# The web chat (no Docker needed), then open http://localhost:8080/chat:
uv run workshop/chat.py
```

## Architecture

```mermaid
flowchart LR
    User(["👤 You"]) -->|/chat| ORCH["🛡️ Orchestrator<br/>Antigravity agent<br/>deny('*') + allow one tool per SRE skill"]
    ORCH -->|"list_incidents · diagnose_sre · write_post_mortem"| SRE["🔬 SRE engine<br/>ADK: TraceAnalyzer → LogCorrelator"]
    SRE -->|tools| T["query_metrics · analyze_trace_cascade<br/>generate_post_mortem"]
    APP["🐒 Target app"] -->|traces · logs · metrics| DATA[("mock_telemetry_data/")]
    T --> DATA
```

In this workshop, "SRE agent" is the diagnostics engine in `sre_agent/`. The diagram and the commands call it "SRE engine".

Facilitators: read [FACILITATOR.md](FACILITATOR.md).
