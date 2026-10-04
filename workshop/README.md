# 🛸 Workshop: Build an Autonomous SRE Agent (90 minutes)

You'll take an SRE agent that *almost* works and finish it: teach it to find the real bottleneck in a
distributed trace, feed it metrics, hand its Gemini-powered correlator the right tools, lock the
user-facing agent down with a deny-by-default safety policy, and surface incident severity in the
chat UI.

Everything runs **on your laptop with no cloud account and no API key**. A `GEMINI_API_KEY` is
optional: with one, the agents reason with Gemini; without one, every agent runs a deterministic
simulation tier that produces the same report structure.

## Before the workshop (10 minutes, please do this at home)

You need **git**, **Python 3.11+** and [**uv**](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/xSAVIKx/sre-agent.git
cd sre-agent
git switch -c my-work step-00      # your own branch, starting at step 0
uv sync --all-packages             # downloads the dependencies (do this on good wifi)
uv run workshop/check.py 0         # checks your setup
```

Optional: `export GEMINI_API_KEY=...` ([get a key](https://aistudio.google.com/apikey)).

## Agenda

| Time      | Step                                                           | You will touch                            |
|:----------|:---------------------------------------------------------------|:------------------------------------------|
| 0:00      | Intro and live demo                                            | –                                         |
| 0:10      | [0 · Setup and tour](steps/00-setup/README.md)                 | –                                         |
| 0:20      | [1 · Find the bottleneck](steps/01-find-the-bottleneck/README.md) | `sre_agent/.../gcp_tools.py`           |
| 0:32      | [2 · Feed the metrics](steps/02-feed-the-metrics/README.md)    | `app/main.py`                             |
| 0:44      | [3 · Give the agent its tools](steps/03-give-the-agent-tools/README.md) | `sre_agent/.../sre_workflow.py`  |
| 0:54      | [4 · Lock the Orchestrator down](steps/04-lock-it-down/README.md) | `agent/.../config.py`                  |
| 1:06      | [5 · Show the severity](steps/05-show-the-severity/README.md)  | `agent/.../a2ui_translator.py`            |
| 1:20      | [6 · Wrap-up: production and next steps](steps/06-wrap-up/README.md) | –                                   |

Every step has a `# TODO(step-N)` in the code. Find yours with:

```bash
git grep -n "TODO(step-1)"
```

## How the steps work

Each step is a **git tag**. `step-00` is the starting point with every TODO open; `step-03` has
steps 1–3 solved; `step-05` is the finished project.

| I want to…                         | Do this                                                              |
|:-----------------------------------|:---------------------------------------------------------------------|
| check my work on step N            | `uv run workshop/check.py N` (red ❌ until solved, then green ✅)    |
| check everything                   | `uv run workshop/check.py all`                                       |
| see the solution for step N        | `git diff step-0<N-1> step-0N -- ':!skills'`                         |
| apply just step N's solution       | `git apply workshop/steps/0N-*/solution.patch`                       |
| catch up to the start of step N+1  | `git stash && git switch -C catch-up step-0N`                        |
| start over                         | `git switch -c fresh step-00`                                        |

No git? Download the repository as a ZIP from the `workshop` branch and use
`patch -p1 < workshop/steps/0N-*/solution.patch` to apply solutions.

The `solution.patch` files also update `skills/sre_incident_solver/`, a generated copy of the SRE
engine (see [step 6](steps/06-wrap-up/README.md)). You never need to edit it.

## The two commands you'll run all day

```bash
# Generate an incident and run the diagnosis in your terminal:
uv run simulate_incident.py                 # through the Orchestrator agent (the real path)
uv run simulate_incident.py --engine-only   # straight to the SRE engine (skips the Orchestrator)

# The web chat (no Docker needed), then open http://localhost:8080/chat:
MOCK_GCP=true uv run uvicorn agent.main:app --port 8080
```

## Architecture in one picture

```mermaid
flowchart LR
    User(["👤 You"]) -->|/chat| ORCH["🛡️ Orchestrator<br/>Antigravity agent<br/>deny('*') + allow one tool per SRE skill"]
    ORCH -->|"list_incidents · diagnose_sre · write_post_mortem"| SRE["🔬 SRE engine<br/>ADK: TraceAnalyzer → LogCorrelator"]
    SRE -->|tools| T["query_metrics · analyze_trace_cascade<br/>generate_post_mortem"]
    APP["🐒 Target app"] -->|traces · logs · metrics| DATA[("mock_telemetry_data/")]
    T --> DATA
```

Facilitators: see [FACILITATOR.md](FACILITATOR.md).
