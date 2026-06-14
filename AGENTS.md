# AI Agent Repository Guidelines (`AGENTS.md`)

This repository is designed for "agent-first" software engineering. If you are an AI agent working
on this codebase, please adhere to the following architectural guidelines and coding standards.

---

## 🏗️ Architecture Overview

The codebase is split into two main sections:

1. **Target Stack (`app/`)**: A FastAPI microservice running OpenTelemetry for logging and tracing.
2. **SRE Skill (`skills/sre_incident_solver/`)**: A reusable Antigravity Agent Skill containing the
   core diagnostics engine and tools.
3. **Standalone Agent Service (`agent/`)**: The runnable FastAPI service wrapper.
    * Reorganized using a standard modern Python structure: source code files reside in `agent/src/agent/` and test cases in `agent/test/`.
    * `google-antigravity` handles OS/GCP access, safety gating (deny-by-default), and HTTP server
      execution via `agent/src/agent/config.py` and `agent/src/agent/main.py`.
    * `google-adk` coordinates the multi-agent graph (Trace Analyzer + Log Correlator) in the SRE
      skill.

---

## 🛠️ Modifying & Adding Tools

### 1. The `@register_tool` Decorator

Do not manually append new tools to the `LocalAgentConfig` tools list. Instead, define your tool in
the `skills/sre_incident_solver/` directory and decorate it with `@register_tool` from `.registry`:

```python
from .registry import register_tool


@register_tool
async def query_my_new_observability_metric(param: str) -> str:
    """Detailed docstring explaining the tool's purpose."""
    # Tool logic here...
```

The config loader dynamically gathers all decorated tools at startup.

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

* **Least-Privilege**: The agent configuration enforces a "deny-by-default" posture using the
  Antigravity policies in `agent/config.py`.
* **Modification Warning**: Do not modify `safety_policies` to bypass user confirmation (e.g.
  allowing write commands without `ask_user("run_command")`) unless explicitly requested by the
  human developer.
* **Hooks**: Customize the `SreToolErrorHook` in `agent/config.py` to handle any specific API
  failures or retry mechanisms.

---

## 📦 Dependency & Build Management

### 1. `uv` Workspace Layout
This repository uses `uv` workspaces to isolate dependencies:
* **Root `pyproject.toml`**: Configures the workspace and links the packages. Do not add core dependencies here.
* **`app/pyproject.toml`**: Stores dependencies for the target stack.
* **`agent/pyproject.toml`**: Stores dependencies for the agent service and core SRE skill.

To synchronize dependencies locally, run:
```bash
uv sync --all-packages
```

### 2. Multi-Stage Docker Optimization
Container setups utilize multi-stage builds to optimize image size and security:
* **Builder Stage**: Installs `uv` to resolve and build the Python virtual environment (`.venv`) cleanly.
* **Runner Stage**: Copies only the pre-compiled `.venv` and source code. `uv` is **not** included in the final runtime container.
* When editing Dockerfiles, preserve this multi-stage separation.

### 3. Running Agent Tests
Since the agent project follows the `src/` and `test/` package layout, run unit tests locally from the workspace root by executing:
```bash
PYTHONPATH=agent/src uv run python -m unittest discover -s agent/test
```

---

## 🐍 Python 3.14 Conventions

* Use native container generics (e.g., `list[str]`, `dict[str, Any]`) instead of importing `List` or
  `Dict` from `typing`.
* Use the union operator `|` for optional types (e.g., `str | None`) rather than `Optional[str]`.
