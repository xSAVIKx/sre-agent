---
name: sre_incident_solver
description: Diagnoses distributed service failures in a GCP stack - scans Cloud Trace for slow or failing requests, finds the bottleneck span (inclusive vs. exclusive time), correlates Cloud Logging entries, and writes an incident post-mortem. Use when a developer reports errors, latency spikes, HTTP 5xx failures or outages in their microservices.
---

# SRE Incident Solver

An autonomous site reliability engineering skill that diagnoses distributed service failures in GCP
stacks.

* **Version**: `0.2.0`
* **Entrypoint**: `sre_workflow.py` (`run_sre_diagnostics`)
* **Language**: `python`

> **Generated code.** The Python modules in this folder are copies of the `sre_agent` package,
> produced by `scripts/sync_skill.py`. Edit `sre_agent/src/sre_agent/` and re-run
> `uv run python scripts/sync_skill.py`; CI fails if the copy is stale.

## Trigger Instructions

Trigger this skill when a developer reports system errors, slow response times, service outages, or
asks to diagnose latency spikes or HTTP 5xx failures in their microservices stack.

## How to Run

From the repository root, with telemetry produced by `uv run simulate_incident.py` (or a real GCP
project and `MOCK_GCP=false`):

```bash
PYTHONPATH=skills:sre_common/src MOCK_GCP=true uv run python -c "
import asyncio
from sre_incident_solver.gcp_tools import query_traces
from sre_incident_solver.sre_workflow import run_sre_diagnostics

async def main():
    print(await run_sre_diagnostics(await query_traces(limit=10)))

asyncio.run(main())"
```

Without `GEMINI_API_KEY` the deterministic tier runs; with it, the ADK + Gemini tier does.

## Dependencies

* **Python Libraries**: Declared in [requirements.txt](./requirements.txt).
* **Workspace-local**: the modules import `sre_common` at module scope. That package is not
  published to PyPI, so `requirements.txt` cannot express it - install it from this repository
  (`pip install -e sre_common`) or put `sre_common/src` on `PYTHONPATH`.
