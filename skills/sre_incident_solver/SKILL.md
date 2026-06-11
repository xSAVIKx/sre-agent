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
