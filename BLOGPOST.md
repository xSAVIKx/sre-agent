# Building a Secure, Autonomous Cloud SRE Agent with Google ADK and Antigravity SDK

* triaging production incidents at 3 AM is one of the most stressful parts of being a developer.
  Distributed systems make this harder: logs are scattered across microservices, traces are
  separated from error messages, and finding the root cause requires manual correlation across
  multiple observability dashboards.
* What if an AI SRE could handle the initial triage for you?
* In this post, we introduce a complete blueprint for building and deploying an **autonomous SRE
  Agent in Google Cloud**. This system combines two powerful Google frameworks: the **Agent
  Development Kit (ADK)** for multi-agent diagnostic graphs, and the **Google Antigravity SDK** for
  runtime safety, tool permissions, and local simulation.

---

## 🏗️ The Hybrid Architecture: ADK + Antigravity

To build a reliable SRE assistant, we need to solve two problems: **diagnostic reasoning** and *
*environmental execution/safety**. Our blueprint splits these responsibilities:

```mermaid
graph TD
    User([User Alert]) -->|Trigger Diagnose| AG_Agent[Antigravity Agent Runtime]
    AG_Agent -->|Safety Gated Tools| GCP_APIs[(Google Cloud Trace & Logging)]
    AG_Agent -->|Invoke ADK Workflow| SRE_Workflow[ADK Diagnostic Graph]

    subgraph ADK Multi-Agent Workflow
        SRE_Workflow --> Trace_Agent[Trace Analyzer Agent]
        Trace_Agent -->|Extracts Trace ID| Log_Agent[Log Correlator Agent]
        Log_Agent -->|Formulates Diagnosis| SRE_Workflow
    end

    SRE_Workflow -->|Returns SRE Report| AG_Agent
    AG_Agent -->|Output Report| User
```

1. **Google ADK (Workflow Orchestration)**:
   The ADK is a code-first library designed for multi-agent systems. We use it to construct a
   workflow containing specialized agents:
    * **Trace Analyzer Agent**: Scans recent traces to isolate latency spikes and extracts the
      anomalous `traceId`.
    * **Log Correlator Agent**: Takes the `traceId` and correlated logs, reads stack traces, and
      analyzes the root cause.
2. **Google Antigravity SDK (Safety & Tool Harness)**:
   The Antigravity SDK handles OS and cloud access, wires python functions into agent tools, and
   enforces safety. By using `LocalAgentConfig`, we define a strict **"deny-by-default"** policy.
   The agent can only read GCP data and must prompt for confirmation (`ask_user("run_command")`)
   before executing any write/modify operations.

---

## 🔍 The Diagnostic Flow: Trace-to-Log Correlation

When a user reports that an API is slow or failing, the SRE agent starts a distributed
investigation:

1. **Trace Scanning**: The agent calls `query_traces` to get recent transactions. It isolates a
   trace with high latency (>10 seconds) or error flags.
2. **Span Hierarchy Analysis**: It calls `get_trace_details(trace_id)` to view the span tree. It
   notices that the API Gateway span is waiting on a downstream Backend service, which is blocked by
   a Database child span taking 10.2 seconds and reporting an error status.
3. **Log Correlation**: The agent extracts the `trace_id` from the failing span and calls
   `query_logs_by_trace(trace_id)`. Because the target microservices write structured JSON logs with
   trace correlation IDs to stdout, Cloud Logging groups them.
4. **Diagnosis**: The Log Correlator agent parses the database error log:
   `ConnectionTimeoutError: Failed to connect to db-primary.gcp.internal:5432 after 10000ms`,
   identifies database connection pool exhaustion, and outputs a complete markdown diagnostic
   report.

---

## 🔒 Least-Privilege Deployment to GCP Cloud Run

In production, an SRE agent must be secure. Giving an AI agent full admin access to your GCP project
is a security risk.

To implement SRE security best practices,
our [deploy.sh](file:///d:/AntigravityProjects/TestAntigravity/deploy.sh) script deploys the
services to **Google Cloud Run** using separate, least-privilege service accounts:

* **Target Application Identity** (`sre-target-app-sa`): Runs the FastAPI app. It is restricted to *
  *write-only** telemetry roles:
    * `roles/cloudtrace.agent` (Send traces to Cloud Trace)
    * `roles/logging.logWriter` (Write logs to Cloud Logging)
* **SRE Agent Identity** (`sre-agent-sa`): Runs the agent service. It is restricted to **read-only**
  telemetry roles:
    * `roles/cloudtrace.user` (Query and view Cloud Trace graphs)
    * `roles/logging.viewer` (Read and filter Cloud Logging logs)

---

## 🔌 Extensible by Design (SigNoz, Grafana, Metabase)

A production SRE environment contains many observability tools. To support expansion, we implement
an extensible tool structure:

* **Tool Registry**: A custom `@register_tool` decorator automatically collects new python tools
  without bloating core config files.
* **Dynamic MCP Loader**: On startup, the agent reads `agent/agent_config.json`. If a third-party
  Model Context Protocol (MCP) server is enabled (like a Grafana MCP or SigNoz MCP), the loader
  dynamically wires it into the agent's capabilities.

---

## 🚀 Get Started

The complete repository template includes a **local standalone simulation mode** that runs in
seconds using `uv`:

```bash
uv run simulate_incident.py
```

This boots the FastAPI application locally in mock mode, generates simulated telemetry files, and
executes the SRE agent diagnostics.

To build, test, and deploy the SRE agent in Google Cloud, check out the repository:

* **GitHub Repository
  **: [TestAntigravity Workspace](file:///d:/AntigravityProjects/TestAntigravity/)
* **Step-by-step Codelab**: [CODELAB.md](file:///d:/AntigravityProjects/TestAntigravity/CODELAB.md)
* **Agent Guidelines**: [AGENTS.md](file:///d:/AntigravityProjects/TestAntigravity/AGENTS.md)
