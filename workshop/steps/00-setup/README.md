# Step 0 · Setup and tour (10 min)

**Goal:** everything installed, and a first look at the system you're about to finish.

## 1. Check your setup

```bash
git switch -c my-work step-00   # if you haven't already
uv sync --all-packages
uv run workshop/check.py 0
```

All imports should say `ok`. `GEMINI_API_KEY: not set` is fine.

## 2. Run the incident

```bash
uv run simulate_incident.py
```

The target app (`app/main.py`) simulates a `Gateway → Backend → Database` request where the
database times out after 10 s, and writes traces and logs to `mock_telemetry_data/`. Then the
**Orchestrator** agent is asked to find the root cause.

Look at the end of the output:

```text
The `diagnose_sre` tool call was blocked by the safety policy (decision: deny).
```

That's on purpose: the Orchestrator currently denies *everything*. You'll fix it in step 4. Until
then, talk to the SRE engine directly:

```bash
uv run simulate_incident.py --engine-only
```

Read the report and spot what's wrong:

* **Identified Bottleneck** names `/api/gateway` with `0 ms` (step 1).
* **Observability Metrics** says `No CPU utilization data available.` (step 2).

## 3. Tour (5 minutes, read along with the facilitator)

| Layer | Where | What it does |
|:--|:--|:--|
| Target app | `app/main.py` | Generates the incident: traces, logs (and soon metrics). |
| Tools | `sre_agent/src/sre_agent/gcp_tools.py` | Plain async Python functions the agents call. Type hints + docstrings become the LLM tool schema. Each has an `if IS_MOCK:` branch that reads local files. |
| SRE engine | `sre_agent/src/sre_agent/sre_workflow.py` | ADK multi-agent graph: `trace_analyzer → fetch_telemetry → log_correlator`, plus a deterministic tier for when there's no API key. |
| Orchestrator | `agent/src/agent/config.py` | The user-facing Antigravity agent. Its only power is the `diagnose_sre` tool, granted by policy. |
| Chat UI | `agent/src/agent/routes.py`, `a2ui_translator.py`, `index.html` | Streams the agent over SSE and renders the report as A2UI components. |

✅ **Done when** `uv run workshop/check.py 0` is all `ok` and you've seen both reports.
