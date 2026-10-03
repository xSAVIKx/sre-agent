# SRE Incident Solver

An autonomous site reliability engineering skill that diagnoses distributed service failures in GCP
stacks.

## Skill Definition

* **Name**: `sre_incident_solver`
* **Version**: `0.1.0`
* **Entrypoint**: `sre_workflow.py`
* **Language**: `python`
* **Description**: Useful for inspecting distributed trace latency, correlating logs, and
  identifying database connection timeouts in GCP.

## Trigger Instructions

This skill should be triggered when a developer reports system errors, slow response times, service
outages, or specifically requests to diagnose latency spikes or HTTP 5xx failures in their
microservices stack.

## Dependencies

* **Python Libraries**: Declared in [requirements.txt](./requirements.txt):
  - `google-adk>=2.11.0,<3`
  - `google-genai>=2.28.0,<3`
  - `google-cloud-trace>=1.20.0`
  - `google-cloud-logging>=3.16.2`
* **Workspace-local**: `gcp_tools.py` imports `sre_common` at module scope. That package
  is not published to PyPI, so `requirements.txt` cannot express it - install it from
  this repository (`pip install -e sre_common`) or put `sre_common/src` on `PYTHONPATH`.
