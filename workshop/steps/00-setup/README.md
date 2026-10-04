# Step 0 · Setup and tour (10 min)

**Goal:** Install all tools. Look at the system that you complete in this workshop.

## 1. Check your setup

1. Do these commands:

   ```bash
   git switch -c my-work step-00   # if you haven't already
   uv sync --all-packages
   uv run workshop/check.py 0
   ```

2. Make sure that each import line shows `ok`.

`GEMINI_API_KEY: not set` is not an error.

## 2. Run the incident

1. Do this command:

   ```bash
   uv run simulate_incident.py
   ```

2. Read the last lines of the output. You see this line:

   ```text
   The `diagnose_sre` tool call was blocked by the safety policy (decision: deny).
   ```

3. Send the request directly to the SRE agent:

   ```bash
   uv run simulate_incident.py --engine-only
   ```

4. Read the report. Find the two errors:

   * **Identified Bottleneck** shows `/api/gateway` with `0 ms`. Step 1 fixes this.
   * **Observability Metrics** shows `No CPU utilization data available.` Step 2 fixes this.

The command does these tasks:

* The target app (`app/main.py`) simulates a `Gateway → Backend → Database` request.
* The database times out after 10 s.
* The target app writes traces and logs to `mock_telemetry_data/`.
* The **Orchestrator** agent gets a request to find the root cause.

The safety policy blocks the tool call on purpose. At this time, the Orchestrator denies *all*
tool calls. You fix this in step 4. Until then, use `--engine-only`.

## 3. Tour (5 minutes, with the facilitator)

| Layer | Location | Function |
|:--|:--|:--|
| Target app | `app/main.py` | Makes the incident: traces and logs. After step 2, it also makes metrics. |
| Tools | `sre_agent/src/sre_agent/gcp_tools.py` | Async Python functions that the agents call. The type hints and docstrings become the LLM tool schema. Each tool has an `if IS_MOCK:` branch that reads local files. |
| SRE agent | `sre_agent/src/sre_agent/sre_workflow.py` | ADK multi-agent graph: `trace_analyzer → fetch_telemetry → log_correlator`. It also has a deterministic simulation for use without an API key. |
| Orchestrator | `agent/src/agent/config.py` | The Antigravity agent that users talk to. It has only three tools, one for each SRE agent skill: `list_incidents`, `diagnose_sre` and `write_post_mortem`. The policy must allow each tool. |
| A2UI surfaces | `sre_agent/src/sre_agent/a2ui_surfaces.py` | Makes an A2UI v0.9 surface for each result. A surface contains catalog components, not HTML. |
| Chat UI | `agent/src/agent/routes.py`, `index.html`, `static/sre-a2ui.js` | Streams the agent output over SSE. Shows the replies and the A2UI surfaces with `@a2ui/lit`. |

✅ **Done when:** `uv run workshop/check.py 0` shows `ok` for all lines, and you read the two
reports.
