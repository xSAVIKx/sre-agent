# Codelab: Building an Autonomous GCP SRE Agent (ADK + Antigravity with `uv`)

This step-by-step codelab guides you through constructing a site reliability engineering (SRE) agent that can troubleshoot distributed application failures in Google Cloud.

## 🎯 What You Will Build
* An instrumented **FastAPI Target Application** that generates distributed traces and correlated logs.
* An **Agent Skill** containing custom python tools that query Cloud Trace and Cloud Logging.
* A **Google ADK Multi-Agent Workflow** that coordinates trace-scanning and log-analysis.
* An **Antigravity SDK Runtime Harness** that wraps the ADK workflow, applies safety policies, and deploys as a Cloud Run API.
* A **Bootstrap and Deploy Pipeline** that enforces least-privilege IAM security.

---

## 🛠️ Prerequisites

* **Python 3.14** installed on your system.
* **`uv`** package manager installed: `pip install uv`.
* **Google Cloud SDK (`gcloud` CLI)** installed and authenticated.
* A GCP Billing account linked (for Cloud Run deployments).

---

## Step 1: Project Bootstrapping with `uv`

Instead of raw `pip` and standard `venv`, we use **`uv`** for extremely fast virtual environment creation and robust package dependency resolution.

### 1. Initialize Project Directory
Create a project folder and generate the `pyproject.toml` file in the root:
```toml
[project]
name = "sre-agent-codelab"
version = "0.1.0"
description = "SRE agent trace & log correlation codelab using Antigravity and ADK"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "google-antigravity>=0.0.4",
    "google-adk>=1.28.1",
    "google-cloud-trace>=1.11.0",
    "google-cloud-logging>=3.8.0",
    "fastapi>=0.110.0",
    "uvicorn>=0.28.0",
    "httpx>=0.27.0",
    "opentelemetry-sdk>=1.24.0",
    "opentelemetry-exporter-google-cloud-trace>=1.24.0",
    "tabulate>=0.9.0",
]
```

### 2. Create the Virtual Environment
```bash
uv venv
```
This initializes a `.venv/` directory. You do not need to manually run `pip install`; `uv` will resolve and sync dependencies automatically when running scripts.

---

## Step 2: Defining the Antigravity Agent Skill

An **Agent Skill** is a packaged, discoverable capability that can be dynamically loaded by the Antigravity ecosystem (including the Antigravity CLI and the Antigravity 2.0 visual desktop app).

Create the directory structure:
```bash
mkdir -p skills/sre_incident_solver
```

### 1. Declare the Skill Metadata
Create `skills/sre_incident_solver/SKILL.md` to define the skill metadata and triggers:
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

### 2. Implement the Extensible Tool Registry
Create `skills/sre_incident_solver/registry.py` to allow dynamic tool loading:
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

## Step 3: Designing Custom GCP SRE Tools

When building agents, **type hints and docstrings are the actual interface definitions** for the LLM. The Antigravity SDK compiles python function signatures directly into LLM tool schemas.

Create `skills/sre_incident_solver/gcp_tools.py` containing three custom tools. They query Cloud Trace and Cloud Logging APIs, falling back to a local directory simulation if `MOCK_GCP=true`:

```python
import os
import json
import logging
from .registry import register_tool

# Setup fail-safe imports of client libraries
try:
    from google.cloud import trace_v2
    from google.cloud import logging as cloud_logging
except ImportError:
    trace_v2, cloud_logging = None, None

IS_MOCK = os.getenv("MOCK_GCP", "true").lower() in ("true", "1") or trace_v2 is None

@register_tool
async def query_traces(project_id: str | None = None, limit: int = 10) -> str:
    """Queries recent traces from GCP Cloud Trace.

    Fetches a list of recent distributed trace summaries, including their
    trace IDs, start times, and root span names.
    """
    # ... mock and real query implementation ...
```

---

## Step 4: Building the ADK Multi-Agent Diagnostics

We use the **Agent Development Kit (ADK)** to orchestrate a multi-agent diagnostic graph:
1. **Trace Analyzer Agent**: Scans traces, isolates high latency/errors, and extracts the anomalous `traceId`.
2. **Log Correlator Agent**: Queries trace-correlated logs and parses error stacktraces.

Create `skills/sre_incident_solver/sre_workflow.py`:
```python
from google.adk import Agent as AdkAgent
from google.adk import Workflow as AdkWorkflow

trace_analyzer = AdkAgent(
    name="trace_analyzer",
    instruction="Extract trace ID representing slowest/failing request. Output ONLY trace ID."
)

log_correlator = AdkAgent(
    name="log_correlator",
    instruction="Analyze trace spans and correlated logs. Find root cause and output SRE report."
)

async def run_sre_diagnostics(traces_json: str) -> str:
    # Executed step-by-step logic
```

---

## Step 5: Wiring the Antigravity Agent Runtime

The Antigravity SDK handles environment interactions, safety policies, and lifecycle hooks. We decouple the runtime execution wrapper into a dedicated `agent/` directory:

1. **Safety Config (`agent/config.py`)**: Defines `deny("*")` to secure the agent, and `allow(...)` specific read-only tools. Any command execution is gated by `ask_user("run_command")`.
2. **HTTP API Server (`agent/main.py`)**: A FastAPI app exposing `/diagnose` and `/health` endpoints.
3. **Configurations (`agent/agent_config.json`)**: Extensible configurations for third-party MCP servers and tools.

---

## Step 6: Developing the FastAPI Example Application

Create `app/main.py`. This simulates a multi-tier microservice: `Gateway -> Backend -> Database`.
* It uses OpenTelemetry to output traces.
* If a query parameter `trigger_error=true` is passed, the database endpoint simulates a connection timeout and throws a `ConnectionTimeoutError`, logging the trace ID.
* It outputs structured stdout JSON logs, which Cloud Run automatically maps to GCP trace IDs.

---

## Step 7: Testing Standalone Local Simulation

We write a launcher `simulate_incident.py` to test the loop locally:
```bash
uv run simulate_incident.py
```
This script executes the FastAPI app internally (generating mock telemetry in `mock_telemetry_data/`), boots the SRE Agent skill locally, executes the ADK diagnostic workflow, and prints the root-cause diagnosis.

---

## Step 8: Production Deployment to Cloud Run

To deploy this in GCP following security best practices, we run a two-part setup:

### 1. Interactive Bootstrapping
Run `./bootstrap.sh` to configure variables:
* Selects or creates your GCP Project.
* Sets preferred regions.
* Configures local `.env`.

### 2. Least-Privilege IAM Deployment
Run `./deploy.sh`. This script:
1. Creates `sre-target-app-sa` (write-only telemetry) and `sre-agent-sa` (read-only telemetry).
2. Assigns IAM roles:
   * App: `roles/cloudtrace.agent`, `roles/logging.logWriter`.
   * Agent: `roles/cloudtrace.user`, `roles/logging.viewer`.
3. Builds the Docker containers via Cloud Build and deploys them to Cloud Run.

### 3. Verify Deployed Agent
Trigger an incident in the cloud:
```bash
curl "https://sre-target-app-<hash>.run.app/api/gateway?trigger_error=true"
```
Call the SRE agent API endpoint `/diagnose`:
```bash
curl -X POST "https://sre-agent-<hash>.run.app/diagnose" \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Gateway service is throwing errors. Find the root cause."}'
```
The deployed agent will query real Cloud Trace and Cloud Logging APIs using its secure service account, run the multi-agent ADK graph in the container, and return a complete root cause analysis!
