# AI Agent Repository Guidelines (`AGENTS.md`)

This repository is designed for "agent-first" software engineering. If you are an AI agent working
on this codebase, please adhere to the following architectural guidelines and coding standards.

---

## 🏗️ Architecture Overview

The codebase is a **`uv` workspace** organized into five packages:

1. **Target Stack (`app/`)**: A FastAPI microservice instrumented with OpenTelemetry. It generates
   the synthetic `Gateway → Backend → Database` incidents that the agent later diagnoses.
2. **SRE Diagnostics Engine (`sre_agent/`)**: The runnable core. Observability tools live in
   `sre_agent/src/sre_agent/gcp_tools.py`, and the ADK multi-agent graph (Trace Analyzer +
   Log Correlator) lives in `sre_agent/src/sre_agent/sre_workflow.py`.
3. **Orchestrator Service (`agent/`)**: The user-facing FastAPI wrapper and web chat UI. The
   `google-antigravity` SDK handles GCP access and safety gating (deny-by-default) via
   `agent/src/agent/config.py` and `agent/src/agent/main.py`. `google-adk` powers the multi-agent
   graph inside the SRE engine.
4. **Inventory Agent (`inventory_agent/`)**: Discovers and caches the project topology (Cloud Run
   services + databases) used to enrich diagnostics.
5. **Shared Library (`sre_common/`)**: Common `otel_trace`, `retry_async`, `setup_logging`, and
   trace-context middleware imported across the services.

> A portable copy of the diagnostics engine also lives under `skills/sre_incident_solver/` as an
> Antigravity Agent Skill (auto-discovered by the Antigravity CLI / desktop app). The running
> services import the `sre_agent` package — **add or modify tools there**, not in the skill mirror.

---

## 🛠️ Modifying & Adding Tools

### 1. The `@register_tool` Decorator

Do not manually append new tools to the agent config's tools list. Instead, define your tool in the
`sre_agent/src/sre_agent/` package (e.g. in `gcp_tools.py`) and decorate it with `@register_tool`:

```python
from sre_agent.registry import register_tool


@register_tool
async def query_my_new_observability_metric(param: str) -> str:
    """Detailed docstring explaining the tool's purpose."""
    # Tool logic here...
```

The config loader dynamically gathers all decorated tools at startup. (The Orchestrator's own
`diagnose_sre` tool is registered the same way via `agent/src/agent/config.py`.)

### 2. Mandatory Docstrings & Types

* **Type Hints**: All function parameters and return types must be fully type-hinted.
* **Docstrings**: Function docstrings are parsed by the Antigravity SDK to compile the tool schemas
  presented to the LLM. If your docstrings are poor or missing, the agent's planner will fail to
  utilize the tool.

### 3. Simulation/Mock Requirements

To preserve local developer convenience, **every tool you add must implement a local mock fallback
**. If `IS_MOCK` is true, the tool must read data from local mock JSON files instead of calling real
cloud APIs:

```python
if IS_MOCK:
    mock_data = _load_mock_file("my_mock_data.json")
    return json.dumps(mock_data)
```

---

## 🔒 Safety Policies & Hooks

* **Least-Privilege**: The Orchestrator enforces a deny-by-default posture via the Antigravity
  policies in `agent/src/agent/config.py` — `[deny("*"), allow("diagnose_sre")]`. Its only
  capability is to delegate to the read-only SRE sub-agent; it cannot read files, run commands, or
  call arbitrary URLs.
* **Modification Warning**: Do not relax this policy (e.g. adding `allow(...)` entries for write,
  terminal, or arbitrary-URL tools) unless explicitly requested by the human developer.
* **Hooks**: Customize the `SreToolErrorHook` in `agent/src/agent/config.py` to handle specific API
  failures or recovery logic.

---

## 📦 Dependency & Build Management

### 1. `uv` Workspace Layout
This repository uses `uv` workspaces to isolate dependencies across five members (`app`, `agent`,
`sre_agent`, `inventory_agent`, `sre_common`):
* **Root `pyproject.toml`**: Configures the workspace and links the packages. Do not add runtime dependencies here.
* **Per-package `pyproject.toml`**: Each member declares its own dependencies (e.g. `app/pyproject.toml`, `sre_agent/pyproject.toml`).

To synchronize dependencies locally, run:
```bash
uv sync --all-packages
```

### 2. Multi-Stage Docker Optimization
Container setups utilize multi-stage builds to optimize image size and security:
* **Builder Stage**: Installs `uv` to resolve and build the Python virtual environment (`.venv`) cleanly.
* **Runner Stage**: Copies only the pre-compiled `.venv` and source code. `uv` is **not** included in the final runtime container.
* When editing Dockerfiles, preserve this multi-stage separation.

### 3. Running Tests
Each package follows the `src/` + `test/` layout, so put its `src` on `PYTHONPATH` when running its
tests from the workspace root:
```bash
PYTHONPATH=agent/src     uv run python -m unittest discover -s agent/test
PYTHONPATH=sre_agent/src uv run python -m unittest discover -s sre_agent/test
```

---

## 🐍 Python Conventions

The workspace targets **Python 3.11+** (`requires-python = ">=3.11"`) and uses modern, 3.14-style typing:

* Use native container generics (e.g., `list[str]`, `dict[str, Any]`) instead of importing `List` or
  `Dict` from `typing`.
* Use the union operator `|` for optional types (e.g., `str | None`) rather than `Optional[str]`.
