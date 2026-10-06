# Step 2 · Give the agent its tools (12 min)

**Goal:** Build the `log_correlator` as a complete ADK agent: a model, an instruction and tools.

## The idea: an agent is a model, an instruction and tools

```python
AdkAgent(name=..., model=..., instruction=..., tools=[...])
```

* **model:** `MODEL` is Gemini when `GEMINI_API_KEY` is set, and the scripted `SimulatedLlm` if not.
* **instruction:** the job of the agent. `LOG_CORRELATOR_INSTRUCTION` is ready in the file.
* **tools:** plain Python functions. ADK reads the **type hints** and the **docstring** of each
  function, and tells the model how to call it. When the model asks for a tool, ADK runs the
  function and gives the result back to the model.

Look at one tool, `query_metrics` in `sre_agent/src/sre_agent/gcp_tools.py`. The docstring is the
documentation for the model:

```python
async def query_metrics(filter_expression: str, duration_minutes: int = 15, project_id: str | None = None) -> str:
    """Queries GCP Cloud Monitoring for time series metrics. ..."""
```

Each tool has an `if IS_MOCK:` branch. On your laptop, it reads `mock_telemetry_data/`, not the
Google Cloud APIs.

At this time, the `log_correlator` has a weak instruction and no tools. It cannot check metrics.

## Your task

1. Find the TODO:

   ```bash
   git grep -n "TODO(step-2)"
   ```

2. Build `log_correlator` with `AdkAgent(...)`:
   * `name="log_correlator"` and `model=MODEL`
   * `instruction=LOG_CORRELATOR_INSTRUCTION`
   * `tools=[...]` with the four tools that the file imports: `query_metrics`,
     `list_metric_descriptors`, `analyze_trace_cascade` and `generate_post_mortem`

## Run it

1. Do these commands:

   ```bash
   uv run workshop/check.py 2
   uv run simulate_incident.py --engine-only
   ```

2. Find the tool calls in the log, before the report:

   ```text
   [GCP Observability] Querying mock metrics for filter 'metric.type="run.googleapis.com/container/cpu/utilizations" ...
   ```

3. Look at **Observability Metrics** in the report. It now shows real values: CPU at `0.24` and
   100 database connections. The connection pool is full: this is the cause of the timeout.

With a `GEMINI_API_KEY`, Gemini decides which tools to call, and writes its own analysis.

## Discuss

* Why does `trace_analyzer` have no tools? Each tool is one more thing that the model can use
  incorrectly, and each tool adds tokens to each call. Give each agent the smallest set of tools
  that it needs. Step 4 applies the same rule to the Orchestrator.
* What makes a good tool docstring? Say what the tool returns, when to use it, and give an example
  of each argument.

## Stretch

* With a key: change `LOG_CORRELATOR_INSTRUCTION`, and compare the reports.
* Write a new tool: an `async` function with type hints and a docstring. Add it to `tools`.

**Stuck?** Run `uv run workshop/step.py hint 2`, or ask the workshop coach in Antigravity. To apply
the solution: `uv run workshop/step.py solve 2`.
