# ADK basics: build agents

**In one sentence:** the [Agent Development Kit (ADK)](https://google.github.io/adk-docs/) is a
Python framework to build agents from a model, an instruction and tools, and to connect agents
in workflows.

**The problem it solves:** a model alone only writes text. An agent must also *do* things: call
APIs, read data, and work with other agents. ADK runs this loop for you: it gives the model the
tools, runs the tool calls that the model asks for, and keeps the history.

## The key elements

### 1. Agent

An agent is a **model**, an **instruction** and (optionally) **tools**. From
`sre_agent/src/sre_agent/sre_workflow.py`:

```python
log_correlator = AdkAgent(
    name="log_correlator",
    model=MODEL,
    instruction=LOG_CORRELATOR_INSTRUCTION,
    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem],
)
```

### 2. Tool

A tool is a plain Python function. ADK reads its **type hints** and its **docstring**, and gives
the model a description of the tool. When the model asks for the tool, ADK runs the function and
gives the result back to the model.

```python
async def query_metrics(filter_expression: str, duration_minutes: int = 15, project_id: str | None = None) -> str:
    """Queries GCP Cloud Monitoring for time series metrics. ..."""
```

The docstring is the documentation for the model. A bad docstring gives bad tool calls.

### 3. Model

The model is a name (`"gemini-3.8-flash"`) or any `BaseLlm` object. You can change the model
without a change to the agent. This project has a scripted model, `SimulatedLlm`
(`sre_agent/src/sre_agent/simulated_llm.py`), for use without an API key:

```python
MODEL = GEMINI_MODEL if os.environ.get("GEMINI_API_KEY") else SimulatedLlm()
```

### 4. Workflow

A workflow is a graph of **nodes**. A node is an agent or a Python function. **Edges** connect
the nodes. A tuple in `edges` is a chain: each node gets the output of the node before it.

```python
AdkWorkflow(name="sre_diagnostics_workflow", edges=[(START, trace_analyzer, fetch_telemetry, log_correlator)])
```

Use a Python function for each step that does not need a model. It is faster, it costs nothing,
and it does not make mistakes.

### 5. Runner, session and events

The **Runner** runs an agent or a workflow. A **session** keeps the history and a **state**
dictionary. Each run gives **events**: text, tool calls and tool results.

```python
runner = Runner(node=sre_diagnostics_workflow, app_name="sre_diagnostics", session_service=session_service)
async for event in runner.run_async(user_id="sre_user", session_id=session.id, new_message=msg):
    ...
```

### 6. Session state and `output_key`

An agent with `output_key="name"` writes its final answer into the session state, under that
name. A later node can read it with `ctx.state["name"]`. Thus, nodes can share values without
a chain of outputs.

### 7. Serve the agent: `to_a2a()`

`to_a2a(agent, agent_card=...)` makes an ADK agent an A2A server. See [A2A basics](a2a.md).

## Try it

```bash
uv run workshop/basics/try_adk.py
```

The script runs the log correlator alone, and shows each event:

```text
🧠 The model asks for a tool: query_metrics({"filter_expression": "metric.type=..."})
🔧 ADK ran query_metrics, and gives the result to the model: '[ { "metric": ...'
💬 The model answers:
## 🔍 Root Cause Analysis ...
📦 Session state after the run: {}
```

Run it one time without `GEMINI_API_KEY` (scripted model) and one time with a key (Gemini).
Compare the tool calls.

## In the workshop

* **Step 1** connects the nodes in a workflow.
* **Step 2** builds the log correlator: model, instruction and tools.
* **Stretch (step 2)** passes a value through the session state with `output_key`.
