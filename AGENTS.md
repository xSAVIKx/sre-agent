# AI Agent Repository Guidelines (`AGENTS.md`)

This file gives the rules for AI agents and human contributors that change this repository.
Follow these rules for architecture, code and tests.

---

## 🏗️ Architecture Overview

The codebase is a **`uv` workspace** with five packages:

| Package | Name in this file | Content |
| :--- | :--- | :--- |
| `app/` | target app | A FastAPI service with OpenTelemetry. It makes the synthetic `Gateway → Backend → Database` incidents that the SRE agent diagnoses. |
| `sre_agent/` | SRE agent | The diagnostics engine. The observability tools are in `sre_agent/src/sre_agent/gcp_tools.py`. The ADK multi-agent workflow (Trace Analyzer + Log Correlator) is in `sre_agent/src/sre_agent/sre_workflow.py`. ADK's `to_a2a()` serves it over A2A (`a2a_agent.py`). |
| `agent/` | Orchestrator | The user-facing FastAPI service and the web chat. The `google-antigravity` SDK runs the agent and applies the deny-by-default tool policies in `agent/src/agent/config.py`. |
| `inventory_agent/` | Inventory agent | Finds and caches the project topology (Cloud Run services and databases). The SRE agent uses the topology in its reports. A plain `a2a-sdk` `AgentExecutor` serves it over A2A (`a2a_server.py`). |
| `sre_common/` | shared library | `otel_trace`, `retry_async`, `setup_logging`, the trace-context middleware, tracing setup and `a2a_client.call_agent`. All services import it. |

### Rule: calls between agents use A2A

All calls from one agent to another agent use the A2A protocol (v1.0, `a2a-sdk`).

* Do not add other HTTP or SSE endpoints between agents.
* Serve an ADK agent with `to_a2a()`. Serve other agents with an `a2a-sdk` `AgentExecutor`.
* Publish an agent card with explicit skills.
* Call an agent with `sre_common.a2a_client.call_agent`.

### Rule: results for people use A2UI, not HTML

Each SRE skill result is Markdown and also an A2UI (v0.9) surface.

* `sre_agent/src/sre_agent/a2ui_surfaces.py` builds the surfaces from the SRE catalog. The SRE
  catalog is the A2UI basic catalog plus `SeverityBadge` and `Download`.
* The SRE agent sends the surfaces as A2A data parts with the media type `application/json+a2ui`.
  It sends them only to callers that send A2UI client capabilities.
* The chat renders the surfaces with `@a2ui/lit`, from the prebuilt bundle
  `agent/src/agent/static/sre-a2ui.js`.

To add a custom component:

1. Add a Pydantic model in `sre_agent/src/sre_agent/a2ui_surfaces.py`.
2. Add a Lit element in `agent/web/src/sre-a2ui.js`.
3. Rebuild the bundle with `cd agent/web && npm ci && npm run build`.
4. Commit the bundle. CI fails when the bundle is not current.

### Rule: change the engine, not the skill mirror

`.agents/skills/sre_incident_solver/` contains a copy of the SRE agent as an Antigravity Agent Skill. The
Antigravity CLI and the desktop app find it automatically. The running services import the
`sre_agent` package.

1. **Add or change tools in `sre_agent`**, not in the skill mirror.
2. After you change `sre_agent`, run `uv run python scripts/sync_skill.py`. This script generates the mirror.
3. The root test suite fails when the mirror is not current.

---

## 🛠️ Modifying & Adding Tools

### 1. The `@register_tool` decorator

Write the tool in the `sre_agent/src/sre_agent/` package (for example in `gcp_tools.py`). Decorate
it with `@register_tool`:

```python
from sre_agent.registry import register_tool


@register_tool
async def query_my_new_observability_metric(param: str) -> str:
    """Detailed docstring explaining the tool's purpose."""
    # Tool logic here...
```

In `sre_agent`, the decorator records the tool in `sre_agent.registry`. No runtime code reads that
registry. To let the model call the tool, also add the tool to the `tools=[...]` list of the
`log_correlator` agent in `sre_agent/src/sre_agent/sre_workflow.py`.

The Orchestrator has its own `@register_tool` in `agent/src/agent/config.py`. There,
`load_agent_config()` collects all decorated tools at startup. The Orchestrator has one tool for
each skill on the SRE agent card: `list_incidents`, `diagnose_sre` and `write_post_mortem`.

### 2. Docstrings and type hints are mandatory

* **Type hints**: Give a type hint to each function parameter and to the return value.
* **Docstrings**: The SDK builds the tool schema for the model from the docstring and the type
  hints. ADK does this for SRE agent tools. The Antigravity SDK does this for Orchestrator tools.
  If the docstring is bad or missing, the model does not use the tool correctly.

### 3. Mock mode is mandatory

Each new tool must have a local mock mode. When `IS_MOCK` is true, the tool must read local mock
JSON files and must not call cloud APIs. `_load_mock_file` reads files from `MOCK_DATA_DIR`
(default `mock_telemetry_data`).

```python
if IS_MOCK:
    mock_data = _load_mock_file("my_mock_data.json")
    return json.dumps(mock_data)
```

---

## 🔒 Safety Policies & Hooks

* **Least privilege**: The Orchestrator uses a deny-by-default policy. The Antigravity policies
  are in `agent/src/agent/config.py`: `deny("*")` plus one `allow(...)` for each SRE skill tool
  (`list_incidents`, `diagnose_sre`, `write_post_mortem`). The Orchestrator can only call the
  read-only SRE agent. It cannot read files, run commands or call other URLs.
* **New SRE skill**: A new SRE skill needs its own Orchestrator tool and its own explicit `allow`.
* **Do not relax the policy**: Do not add `allow(...)` entries for write, terminal or
  arbitrary-URL tools. Do this only when the human developer explicitly asks for it.
* **Hooks**: To handle API failures or recovery, change `SreToolErrorHook` in
  `agent/src/agent/config.py`.

---

## 📦 Dependency & Build Management

### 1. `uv` workspace layout

The workspace has five members: `app`, `agent`, `sre_agent`, `inventory_agent` and `sre_common`.

* **Root `pyproject.toml`**: Configures the workspace and links the packages. Do not add runtime dependencies here.
* **Package `pyproject.toml`**: Each member declares its own dependencies (for example `app/pyproject.toml`, `sre_agent/pyproject.toml`).

To install the dependencies locally, run:
```bash
uv sync --all-packages
```

### 2. Docker images: shared dependency base

The four services use one shared dependency image, `docker/base.Dockerfile`.

* **Base image**: The build has two stages. The builder stage installs `uv` and runs
  `uv sync --frozen --no-dev --all-packages --no-install-workspace`. The runtime stage copies only
  the `.venv`. No runtime image contains `uv`.
* **Service images** (`<service>/Dockerfile`) start `FROM ${BASE_IMAGE}` and copy only source code.
  Do not install dependencies in a service Dockerfile. Add them to the `pyproject.toml` of the
  package and to `uv.lock`. This change gives the base image a new tag.
* **Tags**: The tag comes from the content. `scripts/base-image.sh tag` calculates a hash of
  `uv.lock` and the base Dockerfile. `.github/workflows/base-image.yml` publishes
  `ghcr.io/xsavikx/sre-agent-base:lock-<hash>` for amd64 and arm64.
* **Base image in `deploy.sh`**: `deploy.sh` uses the published image when it exists. If not, it
  uses a copy in Artifact Registry. If that copy does not exist, Cloud Build builds the base image.
  Thus a change to `uv.lock` always gets current dependencies.
* **Cloud Build**: The root `cloudbuild.yaml` builds and deploys all services in parallel. The
  services find each other through the deterministic Cloud Run URLs
  `https://<service>-<project number>.<region>.run.app`.
* **docker-compose**: It builds the base image locally (`additional_contexts: base: service:base`).

### 3. Running tests

Each package uses the `src/` + `test/` layout. When you run its tests from the workspace root, put
its `src` directory on `PYTHONPATH`. The root suite `test/` contains a workspace-wide import smoke
test. It also contains tests for `app/`, `inventory_agent/`, `sre_common/` and the
`.agents/skills/sre_incident_solver/` mirror.

```bash
PYTHONPATH=agent/src     uv run python -m unittest discover -s agent/test
PYTHONPATH=sre_agent/src uv run python -m unittest discover -s sre_agent/test
uv run python -m unittest discover -s test
```

Most tests use the `try/except ImportError` fallbacks (mocks). Two contract tests are different:
`agent/test/test_sdk_contract.py` and `sre_agent/test/test_sdk_contract.py`. They test the
*real* installed `google-antigravity` and `google-adk`. Without them, an upstream rename can put
the agents in simulation mode and no test fails. When you change an SDK call, update the contract
test also.

### 4. Linting and formatting

The root `pyproject.toml` configures `ruff` (`target-version = "py311"`, `line-length = 120`).
Before you commit, make sure that the two commands report no problems:
```bash
uv run ruff check .
uv run ruff format --check .   # drop --check to apply
```

### 5. Continuous integration

`.github/workflows/ci.yml` has these jobs:

| Job | Checks |
| :--- | :--- |
| `ruff` | `ruff check` and `ruff format --check`. It also runs `workshop/build_steps.py` and fails when the workshop patches change. |
| `tests` | The three test suites, the `simulate_incident.py` smoke test and the `workshop/basics/` try-it scripts, on **Python 3.11 and 3.14**. |
| `installer` | Runs `install.sh` (Linux, macOS) and `install.ps1` (Windows PowerShell 5.1) on a new runner, then `workshop/check.py all` and the simulation. Keep the installers' `UV_VERSION` equal to the CI `UV_VERSION`. |
| `A2UI renderer bundle` | Rebuilds `agent/src/agent/static/sre-a2ui.js` and fails when the committed bundle is different. |
| `docker images` | Runs `docker compose build` for the base image and all service images. |

The `Docs` workflow (`.github/workflows/docs.yml`) builds the website in strict mode on each PR, and
publishes it to GitHub Pages from `master`.

Python 3.11 is the declared floor. Python 3.14 is the version in the Dockerfiles. Ruff runs one
time only, because `target-version = "py311"` sets its rules for all interpreters. Dependabot keeps
the declared floors and `uv.lock` current. See `.github/dependabot.yml`.

---

## 🎓 The workshop

`workshop/` is a 90-minute workshop on the finished code. Its website is built from the same
Markdown.

* **The finished code is the only source.** `workshop/build_steps.py` has a solution/starter pair
  for each `TODO(step-N)`. It generates `workshop/steps/0N-*/solution.patch` and (with
  `--branch`) the local `workshop` branch and the tags `step-00` … `step-05`. Do not edit the
  patches or the branch by hand.
* **When you change code that a step covers**, update the `solution` (and `starter`) string in
  `build_steps.py`. Then run `uv run python workshop/build_steps.py` and commit the patches. CI
  fails when they are stale.
* **Each step is independent:** its tests (`workshop/check.py`, `STEP_TESTS`) fail only because of
  its own TODO. `uv run workshop/preflight.py` checks the full ladder at each tag, and what
  attendees download from GitHub.
* **Docs:** the step READMEs, `workshop/basics/`, `INSTALL.md` and `EXERCISES.md` are the website.
  Check them with `uv run scripts/build_docs.py` (strict: a broken link fails). The build adds a
  collapsed hint and solution to each step page from `build_steps.py` and the `solution.patch`
  files, so do not write solutions into the READMEs.
* **Setup prompt:** `workshop/setup-prompt.txt` is the copy-paste prompt that lets an agent install
  the workshop from an empty folder. `README.md`, `INSTALL.md` and `workshop/README.md` show it
  between `setup-prompt` markers; a test keeps the copies equal. Change the file, then the copies.
* **Agent skills** in `.agents/skills/`: `sre-agent-setup` (install), `sre-workshop-coach`
  (attendees), `sre-workshop-facilitator` (prepare, publish, run, clean up), `sre-agent-deploy`
  (Cloud Run). When you change a step, a command or a file name, update these skills too.

---

## 🐍 Python Conventions

The workspace supports **Python 3.11 and later** (`requires-python = ">=3.11"`). Use modern typing
syntax. CI tests Python 3.11 and 3.14, so code that needs Python 3.12 or later fails the build.

* Use native container generics (for example `list[str]`, `dict[str, Any]`). Do not import `List` or
  `Dict` from `typing`.
* Use the union operator `|` for optional types (for example `str | None`). Do not use `Optional[str]`.
