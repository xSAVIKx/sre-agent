# Autonomous GCP SRE Agent (ADK + Antigravity with `uv`)

This repository contains a complete, production-grade template showcasing how to build and deploy an
autonomous **Site Reliability Engineering (SRE) Agent** in Google Cloud. It combines the **Google
Agent Development Kit (ADK)** for multi-agent diagnostic orchestration, and the **Google Antigravity
SDK** for OS-level safety, tool permissions, and runtime execution.

The agent is designed to troubleshoot distributed microservices by scanning **Cloud Trace**
timelines to isolate latency/errors, finding the root trace ID, and querying correlated log entries
from **Cloud Logging** to pinpoint the exact root cause.

---

## 📖 Key Deliverables

* **Step-by-step Tutorial**: [CODELAB.md](file:///d:/AntigravityProjects/TestAntigravity/CODELAB.md)
* **Editorial Technical Post
  **: [BLOGPOST.md](file:///d:/AntigravityProjects/TestAntigravity/BLOGPOST.md)
* **AI Agent Instructions**: [AGENTS.md](file:///d:/AntigravityProjects/TestAntigravity/AGENTS.md)

---

## 📂 Repository Directory Layout

```
.
├── .gitignore
├── README.md               # Repo homepage
├── AGENTS.md               # Guidelines for AI agent collaborators
├── CODELAB.md              # Step-by-step SRE Codelab
├── BLOGPOST.md             # High-impact technical blog post
├── pyproject.toml          # Python dependencies managed by uv
├── bootstrap.sh            # Interactive GCP project setup script
├── deploy.sh               # Least-privilege GCP Cloud Run deploy script
├── simulate_incident.py    # Local standalone simulation script
│
├── app/                    # Target FastAPI Application (Instrumented)
│   ├── main.py             # App main code with OpenTelemetry
│   └── Dockerfile          # Container config for the target application
│
├── agent/                  # Standalone SRE Agent Service wrapper
│   ├── Dockerfile          # Container config for SRE Agent HTTP API
│   ├── agent_config.json   # MCP and tool extension configurations
│   ├── config.py           # Antigravity Agent safety policies & runtime loader
│   └── main.py             # FastAPI service wrapper for Cloud Run
│
└── skills/                 # Reusable Antigravity Agent Skills
    └── sre_incident_solver/
        ├── SKILL.md        # Skill discovery metadata
        ├── sre_workflow.py # ADK multi-agent orchestration
        ├── gcp_tools.py    # Trace and Log query tools (with mock fallback)
        └── registry.py     # Extensible tool decorator registry
```

---

## 🚀 Quickstart: Local Standalone Simulation

You can run the entire diagnostic workflow locally in seconds using **`uv`**. No GCP account,
project, or credentials required!

### 1. Prerequisites

Ensure you have `uv` installed. If not, run:

```bash
pip install uv
```

### 2. Run the Simulation

```bash
uv run simulate_incident.py
```

This single command:

1. Provisions a virtual environment (`.venv`) and installs all dependencies in `pyproject.toml`.
2. Triggers the mock target app gateway to generate a synthetic incident.
3. Writes mock traces and logs to a local directory (`mock_telemetry_data/`).
4. Boots the SRE agent, which queries the mock data, runs the ADK workflow, and prints out a
   markdown-formatted diagnostic report.

---

## ☁️ Production Deployment: Google Cloud Run

To run this in Google Cloud, we follow Cloud Run security best practices by deploying the target app
and agent with separate, least-privilege service accounts:

### 1. Bootstrap GCP Settings

Run the interactive bootstrapper to set up your project, authenticate, and choose regions:

```bash
./bootstrap.sh
```

### 2. Deploy the Services

```bash
./deploy.sh
```

This script automates:

* Enabling Cloud Run, Cloud Build, Trace, and Logging APIs.
* Provisioning separate service accounts:
    * `sre-chaos-monkey-sa`: Granted **write-only** trace/log roles (`roles/cloudtrace.agent`,
      `roles/logging.logWriter`).
    * `sre-agent-sa`: Granted **read-only** trace/log roles (`roles/cloudtrace.user`,
      `roles/logging.viewer`).
* Building the containers with Cloud Build and deploying both to Cloud Run.

---

## ⚙️ The Antigravity Ecosystem Three Pillars

This codebase highlights how to interact with the SRE agent using the different interfaces in the
Antigravity ecosystem:

### 1. The Antigravity SDK

Used programmatically in `agent/config.py` to define the agent's behavior. We configure
`LocalAgentConfig` with system instructions, register custom python tools, and declare security
policies (e.g. denying all write operations by default and requiring human confirmation for shell
execution).

### 2. The Antigravity CLI

Useful for developers to manage and inspect skills from the command line. You can load this
workspace skill directly using:

```bash
antigravity skills list
antigravity run agent/config.py --prompt "Diagnose recent latency spikes"
```

### 3. Antigravity 2.0 (Visual Workspace)

The visual desktop application automatically discovers skills placed in the `skills/` directory.
When you open this repository in the Antigravity 2.0 Desktop app, the `sre_incident_solver` skill is
immediately recognized via `skills/sre_incident_solver/SKILL.md` and added to your visual canvas,
allowing you to run and audit SRE tasks in a graphical dashboard.

---

## 🧹 Tearing Down the Demo

To prevent any ongoing costs, you can delete all GCP resources (Cloud Run services, service accounts, and IAM roles) created during the deployment by running the cleanup script:
```bash
./cleanup.sh
```
