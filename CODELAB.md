# Codelab: Building an Autonomous GCP SRE Agent (ADK + Antigravity with `uv`)

This step-by-step codelab guides you through constructing an autonomous site reliability engineering (SRE) agent that diagnoses distributed application failures and generates download-ready incident post-mortems.

---

## 🎯 What You Will Build

1. An instrumented **FastAPI Target Application** representing a multi-tier microservice architecture (`Gateway -> Backend -> Database`) that generates OpenTelemetry traces and correlated logs.
2. An **Agent Skill** containing custom python tools that query Cloud Trace and Cloud Logging, perform cascade latency analysis, and generate post-mortem reports.
3. A **Google ADK Multi-Agent Workflow** that coordinates trace scanning and log correlation.
4. An **Antigravity SDK Runtime Harness** that enforces safety policies, translates diagnostic markdown reports to rich A2UI schema elements, and serves a premium Web UI.
5. An **Interactive Local Simulation** and a **Least-Privilege GCP Deployment Pipeline**.

---

## 🛠️ Prerequisites

* **Python 3.14** installed on your system.
* **`uv`** package manager installed: `pip install uv`.
* **Google Cloud SDK (`gcloud` CLI)** installed and authenticated.
* A GCP Billing account linked (if deploying to Google Cloud).

---

## Step 1: Project Bootstrapping with `uv` Workspaces

We use **`uv`** for workspace dependency isolation and extremely fast virtual environment creation.

### 1. Initialize Workspaces

Create a root [pyproject.toml](file:///home/xsavikx/AntigravityProjects/sre-agent/pyproject.toml) configuration linking the target app, agent wrapper, and SRE package:

```toml
[project]
name = "sre-agent-codelab-workspace"
version = "0.1.0"
description = "SRE agent trace & log correlation codelab workspace"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[tool.uv.workspace]
members = ["app", "agent", "sre_agent"]
```

### 2. Create the Workspace Virtual Environment

Execute the following commands in the workspace root:

```bash
uv venv
uv sync --all-packages
```

`uv` automatically creates a shared `.venv/` directory, resolves cross-package configurations, and links local packages together.

> [!TIP]
> **Why `uv`?** `uv` runs workspace synchronization up to 10-100x faster than traditional `pip` inside virtual environments, offering reliable lockfile generation across packages.

---

## Step 2: Defining the Antigravity Agent Skill

An **Agent Skill** is a package that can be dynamically discovered by the Antigravity CLI and the Antigravity 2.0 visual desktop app.

Create the skill definition folder:
```bash
mkdir -p skills/sre_incident_solver
```

### 1. Declare the Skill Metadata

Create [SKILL.md](file:///home/xsavikx/AntigravityProjects/sre-agent/skills/sre_incident_solver/SKILL.md):

```markdown
# SRE Incident Solver

An autonomous site reliability engineering skill that diagnoses distributed service failures in GCP stacks.

## Skill Definition

* **Name**: `sre_incident_solver`
* **Version**: `0.1.0`
* **Entrypoint**: `sre_workflow.py`
* **Language**: `python`
* **Description**: Useful for inspecting distributed trace latency, correlating logs, and identifying database connection timeouts in GCP.
```

### 2. Implement the Tool Registry

Create [registry.py](file:///home/xsavikx/AntigravityProjects/sre-agent/skills/sre_incident_solver/registry.py) to enable decorators for dynamic tool loading:

```python
from typing import Callable, Any

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: list[Callable[..., Any]] = []

    def register(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if func not in self._tools:
            self._tools.append(func)
        return func

    def get_tools(self) -> list[Callable[..., Any]]:
        return self._tools

registry = ToolRegistry()

def register_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    return registry.register(func)
```

---

## Step 3: Designing Custom SRE Tools

Type hints and docstrings are parsed by the Antigravity SDK to compile tool schemas presented to the LLM. 

Create [gcp_tools.py](file:///home/xsavikx/AntigravityProjects/sre-agent/skills/sre_incident_solver/gcp_tools.py) containing:
1. `query_traces` & `get_trace_details`: Fetches trace lists and detailed span timelines.
2. `query_logs_by_trace`: Collects correlated logging stdout entries.
3. `analyze_trace_cascade` (Feature #2): Analyzes child spans and calculates inclusive vs. exclusive execution timings.
4. `generate_post_mortem` (Feature #5): Automates incident post-mortems.

Let's look at the implementation of **exclusive execution duration** in `analyze_trace_cascade`:

```python
@register_tool
async def analyze_trace_cascade(trace_id: str, project_id: str | None = None) -> str:
    """Analyzes a trace to calculate inclusive vs exclusive duration for each span and locate the bottleneck.

    Args:
        trace_id: The unique hex string identifying the trace (32 characters).
        project_id: The GCP Project ID. If None, uses default project.
    """
    # 1. Fetch trace data (mock or real Cloud Trace)
    details_str = await get_trace_details(trace_id, project_id)
    data = json.loads(details_str)
    spans = data.get("spans", [])

    # 2. Build parent-child relationships
    span_map = {s["spanId"]: s for s in spans}
    children_map = {s["spanId"]: [] for s in spans}
    for s in spans:
        parent_id = s.get("parentSpanId")
        if parent_id and parent_id in span_map:
            children_map[parent_id].append(s["spanId"])

    # 3. Calculate exclusive duration (inclusive duration minus sum of child inclusive durations)
    inclusive_durations = {s["spanId"]: _calculate_duration_ms(s["startTime"], s["endTime"]) for s in spans}
    exclusive_durations = {}
    for s in spans:
        span_id = s["spanId"]
        child_ids = children_map[span_id]
        child_durations_sum = sum(inclusive_durations[cid] for cid in child_ids)
        exclusive_durations[span_id] = max(0, inclusive_durations[span_id] - child_durations_sum)

    # 4. Format results as a markdown bottleneck contribution table...
```

---

## Step 4: Building the ADK Multi-Agent Diagnostics

We use the **Agent Development Kit (ADK)** to orchestrate a multi-agent diagnostic graph containing a `trace_analyzer` and a `log_correlator`.

Create [sre_workflow.py](file:///home/xsavikx/AntigravityProjects/sre-agent/skills/sre_incident_solver/sre_workflow.py) defining the graph:

```python
from google.adk import Agent as AdkAgent
from google.adk import Workflow as AdkWorkflow

trace_analyzer = AdkAgent(
    name="trace_analyzer",
    instruction="Extract trace ID representing slowest/failing request. Output ONLY trace ID."
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction="Analyze trace spans and correlated logs. Find root cause and output SRE report.",
    tools=[query_metrics, list_metric_descriptors, analyze_trace_cascade, generate_post_mortem]
)

# Set up edges
sre_diagnostics_workflow = AdkWorkflow(
    name="sre_diagnostics_workflow",
    edges=[
        (START, trace_analyzer, fetch_telemetry, log_correlator)
    ]
)
```

---

## Step 5: Wiring the Antigravity Agent Runtime

The Antigravity SDK enforces security bounds. We declare these in [config.py](file:///home/xsavikx/AntigravityProjects/sre-agent/agent/src/agent/config.py):

```python
# Deny all access by default
safety_policies = [
    deny("*"),
    allow("read_file", target="/home/xsavikx/AntigravityProjects/sre-agent/mock_telemetry_data/*"),
    # Only allow safe read-only observability tools
    allow("execute_url", target="*.googleapis.com"),
]
```

### 1. The A2UI Translator
To enable UI-friendly rendering and downloading, we implement [a2ui_translator.py](file:///home/xsavikx/AntigravityProjects/sre-agent/agent/src/agent/a2ui_translator.py) which intercepts SRE reports containing post-mortem markdown and wraps them in an interactive A2UI schema containing a `download_button` component:

```python
if "# 🚨 Incident Post-Mortem" in text:
    return {
        "type": "container",
        "components": [
            {
                "type": "alert",
                "level": "success",
                "title": "Incident Post-Mortem",
                "text": "The SRE diagnostics agent has auto-generated the incident post-mortem report."
            },
            {
                "type": "section",
                "title": "Document Preview",
                "content": text
            },
            {
                "type": "download_button",
                "text": "Download Post-Mortem Markdown",
                "filename": "post_mortem.md",
                "content": text
            }
        ]
    }
```

### 2. Rendering the Download Button
The frontend [index.html](file:///home/xsavikx/AntigravityProjects/sre-agent/agent/src/agent/index.html) renders this component using HSL colors and interactive translateY transitions, prompting a client-side download:

```javascript
case 'download_button':
    const btn = document.createElement('button');
    btn.innerHTML = `📥 ${comp.text}`;
    btn.style.background = 'linear-gradient(135deg, var(--success) 0%, #059669 100%)';
    btn.style.boxShadow = '0 4px 12px var(--success-glow)';
    btn.style.transition = 'all 0.2s ease';
    btn.onmouseover = () => {
        btn.style.transform = 'translateY(-1px)';
        btn.style.boxShadow = '0 6px 16px rgba(16, 185, 129, 0.35)';
    };
    btn.onclick = () => {
        const blob = new Blob([comp.content], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = comp.filename;
        document.body.appendChild(a);
        a.click();
        URL.revokeObjectURL(url);
    };
    containerDiv.appendChild(btn);
    break;
```

---

## Step 6: Testing Standalone Local Simulation

We write a simulator [simulate_incident.py](file:///home/xsavikx/AntigravityProjects/sre-agent/simulate_incident.py) to run the loop locally.

### Run the Simulation
```bash
uv run simulate_incident.py
```

> [!NOTE]
> **Expected Terminal Output**: The output must display a structured breakdown table detailing that `/api/database` was slow and had a Contribution of `99.3%` due to a `ConnectionTimeoutError`, followed by the full `# 🚨 Incident Post-Mortem` markdown report.

---

## Step 7: Production Deployment to Cloud Run

To run in Google Cloud Run following security best practices, we deploy the services with separate service identities.

### 1. Interactive Bootstrapping
Run `./bootstrap.sh` to configure environment details:
* Authenticate via `gcloud auth login`.
* Set your active GCP Project ID.
* Configure the default region.

### 2. Build & Deploy
Run `./deploy.sh` to build containers using Cloud Build, deploy to Cloud Run, and assign least-privilege roles:
* App service account: `roles/cloudtrace.agent` (write-only)
* Agent service account: `roles/cloudtrace.user` (read-only)

---

## Step 8: Interactive Verification & Verification Checklist

Once deployed, trigger a live incident to check the setup:

1. **Trigger Error**: `curl "https://target-app-<hash>.run.app/api/gateway?trigger_error=true"`
2. **Access Agent UI**: Navigate to `https://orchestrator-agent-<hash>.run.app/chat`.
3. **Ask Agent**: Type `Diagnose the recent latency spikes and generate a post-mortem.`
4. **Download**: Once the agent completes, verify that a green **Download Post-Mortem Markdown** button renders in the chat and exports the markdown report successfully on click.

---

## Step 9: GCP Resource Cleanup

To avoid charges, clean up all provisioned GCP services, service accounts, and IAM policy bindings by running:
```bash
./cleanup.sh
```
