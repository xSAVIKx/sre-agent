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

### 2. Docker Images: Shared Dependency Base
All four services run on one shared dependency image, `docker/base.Dockerfile`:
* **Base image**: a multi-stage build. The builder stage installs `uv` and runs
  `uv sync --all-packages --no-install-workspace`; the runtime stage copies only the `.venv`.
  `uv` is **not** included in any runtime image.
* **Service images** (`<service>/Dockerfile`) are `FROM ${BASE_IMAGE}` and only copy source code.
  Never install dependencies in a service Dockerfile: add them to the package's `pyproject.toml`
  and `uv.lock`, which changes the base image's tag.
* **Tags** are content-addressed: `scripts/base-image.sh tag` hashes `uv.lock` and the base
  Dockerfile. `.github/workflows/base-image.yml` publishes
  `ghcr.io/xsavikx/sre-agent-base:lock-<hash>` (amd64 + arm64). `deploy.sh` uses the published
  image when it exists and builds it in the pipeline when it doesn't, so a lock change never ships
  stale dependencies.
* **Cloud Build**: the root `cloudbuild.yaml` builds and deploys all services in parallel, wired
  together through Cloud Run's deterministic `https://<service>-<project number>.<region>.run.app`
  URLs. docker-compose builds the base locally (`additional_contexts: base: service:base`).

### 3. Running Tests
Each package follows the `src/` + `test/` layout, so put its `src` on `PYTHONPATH` when running its
tests from the workspace root. There is also a workspace-wide import smoke test at the root, which
covers `app/`, `inventory_agent/`, `sre_common/` and the `skills/sre_incident_solver/` mirror — the
trees with no unit tests of their own:
```bash
PYTHONPATH=agent/src     uv run python -m unittest discover -s agent/test
PYTHONPATH=sre_agent/src uv run python -m unittest discover -s sre_agent/test
uv run python -m unittest discover -s test
```

`agent/test/test_sdk_contract.py` and `sre_agent/test/test_sdk_contract.py` are a deliberate
exception to the mock-everything rule: they assert against the *real* installed `google-antigravity`
and `google-adk`. Every other test runs against the `try/except ImportError` fallbacks, so without
them an upstream rename degrades the agents to their simulated mode silently. If you change an SDK
call site, update the contract test with it.

### 4. Linting & Formatting
`ruff` is configured in the root `pyproject.toml` (`target-version = "py311"`, `line-length = 120`).
Both commands must be clean before you commit:
```bash
uv run ruff check .
uv run ruff format --check .   # drop --check to apply
```

### 5. Continuous Integration
`.github/workflows/ci.yml` runs the three test suites and the local `simulate_incident.py` smoke
test on **Python 3.11 and 3.14** — the declared floor and the version the Dockerfiles actually ship.
`ruff check` + `ruff format --check` run once, in a separate job: `target-version = "py311"` fixes
the rules ruff applies, so its verdict does not depend on the interpreter it runs under and a second
pass would only cost CI time. Dependabot keeps the declared floors and `uv.lock` current; see
`.github/dependabot.yml`.

---

## 🐍 Python Conventions

The workspace targets **Python 3.11+** (`requires-python = ">=3.11"`) and uses modern, 3.14-style
typing. CI tests both ends of that range, so a 3.12+-only construct will fail the build rather than
reach a 3.11 user:

* Use native container generics (e.g., `list[str]`, `dict[str, Any]`) instead of importing `List` or
  `Dict` from `typing`.
* Use the union operator `|` for optional types (e.g., `str | None`) rather than `Optional[str]`.
