# 🛸 Autonomous Cloud SRE Agent (ADK + Antigravity with `uv`)

Welcome to the production-grade template for building, testing, and deploying an autonomous **Site Reliability Engineering (SRE) Agent** in Google Cloud. This system integrates the **Google Agent Development Kit (ADK)** for multi-agent diagnostic graphs and the **Google Antigravity SDK** for OS-level sandboxing, tool verification, and safe execution.

The agent monitors microservices, queries distributed traces, correlates logs, diagnoses bottlenecks, auto-generates comprehensive incident post-mortems, and lets you download them instantly from the chat control UI.

---

## ⚡ Featured Capabilities

### 1. ⛓️ Multi-Service Cascade Latency & Bottleneck Analyzer
When a request spikes in latency, the SRE Agent dissects the distributed trace. It calculates the **inclusive vs. exclusive (self) execution time** for every child span, rendering a detailed contribution table to pin down the exact service bottleneck.

### 2. 📄 Automated Incident Post-Mortem Generator
Following a diagnosis, the agent compiles a complete **Incident Post-Mortem (RCA)** including:
* **Incident Overview**: Date/time, root service, Trace ID, impact duration, and status.
* **Timeline**: Trace timestamps indicating gateway alerts, cascading failures, and mitigations.
* **Root Cause Analysis**: Analysis of connection states, active chaos injections, or infrastructure timeouts.
* **Prevention Plan**: Immediate remediation steps, short-term workarounds, and long-term preventions.

### 3. 📥 Interactive Chat Downloader
The web chat interface automatically parses diagnostic reports. If it detects a generated post-mortem, it renders a premium, styled **Download Button** with smooth shadow effects and hover transitions, allowing you to export the post-mortem directly to markdown (`post_mortem.md`) via client-side Blob APIs.

---

## 📂 Repository Layout

```
.
├── .gitignore
├── .gcloudignore            # Global deployment exclusions
├── README.md               # Repo homepage
├── AGENTS.md               # Guidelines for AI agent collaborators
├── CODELAB.md              # Step-by-step SRE Codelab
├── BLOGPOST.md             # High-impact technical blog post
├── pyproject.toml          # Root-level uv workspace configuration
├── bootstrap.sh            # Interactive GCP project setup script
├── deploy.sh               # Least-privilege GCP Cloud Run deploy script
├── cleanup.sh              # Automatic GCP resource teardown script
├── simulate_incident.py    # Local standalone simulation script
│
├── app/                    # Target FastAPI Application (Instrumented)
│   ├── main.py             # App main code with OpenTelemetry
│   ├── Dockerfile          # Multi-stage optimized Docker config
│   ├── .dockerignore       # App Docker build exclusions
│   ├── .gcloudignore       # App Cloud Build exclusions
│   └── pyproject.toml      # Target app dependencies
│
├── agent/                  # Standalone SRE Agent Service wrapper
│   ├── src/agent/
│   │   ├── a2ui_translator.py # Translates raw markdown to rich A2UI schema
│   │   ├── config.py       # Antigravity Agent safety policies & runtime loader
│   │   ├── index.html      # Premium visual chat control interface
│   │   └── main.py         # FastAPI service wrapper for Cloud Run
│   ├── test/
│   │   ├── test_a2ui_translator.py # Unit tests for rendering & download schemas
│   │   └── test_firestore_strategy.py
│   └── pyproject.toml      # Agent service & UV workspace dependencies
│
├── sre_agent/              # Standalone SRE Diagnostics package
│   ├── src/sre_agent/
│   │   ├── gcp_tools.py    # Trace and Log query tools, including cascade analysis
│   │   ├── sre_workflow.py # ADK multi-agent orchestration
│   │   └── registry.py     # Decorator for dynamic tool registration
│   ├── test/
│   │   ├── test_gcp_tools.py # Unit tests for query, cascade, and post-mortem tools
│   │   └── test_itinerary.py
│   └── pyproject.toml      # Standalone SRE dependencies
│
└── skills/                 # Reusable Antigravity Agent Skills
    └── sre_incident_solver/
        ├── SKILL.md        # Skill discovery metadata
        ├── sre_workflow.py # Core ADK multi-agent orchestration
        ├── gcp_tools.py    # Direct GCP trace & logging tool integrations
        └── registry.py     # Decorator for dynamic tool registration
```

---

## 📖 Key Deliverables

* **Step-by-step Tutorial**: Learn how to build this agent from scratch in [CODELAB.md](file:///home/xsavikx/AntigravityProjects/sre-agent/CODELAB.md)
* **Editorial Technical Post**: Read about the engineering architecture in [BLOGPOST.md](file:///home/xsavikx/AntigravityProjects/sre-agent/BLOGPOST.md)
* **AI Agent Instructions**: See rules and conventions in [AGENTS.md](file:///home/xsavikx/AntigravityProjects/sre-agent/AGENTS.md)

---

## 🚀 Quickstart: Local Standalone Simulation

You can run the entire diagnostic workflow locally in seconds using **`uv`**. No GCP account, project, or cloud credentials are required!

### 1. Synchronize Dependencies
Ensure you have `uv` installed, then run the synchronizer:
```bash
uv sync --all-packages
```

### 2. Run the Incident Simulation
```bash
uv run simulate_incident.py
```

This single command:
1. Triggers the mock target app gateway to generate a synthetic incident.
2. Writes mock traces and logs to a local directory (`mock_telemetry_data/`).
3. Launches the SRE agent in mock telemetry mode.
4. Performs a multi-service cascade analysis and generates a complete Markdown post-mortem report, outputting them straight to your terminal.

---

## ☁️ Production Deployment: Google Cloud Run

To run this in Google Cloud, we follow Cloud Run security best practices by deploying the target app and agent with separate, least-privilege service accounts:

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
    * `sre-chaos-monkey-sa`: Granted **write-only** trace/log roles (`roles/cloudtrace.agent`, `roles/logging.logWriter`).
    * `sre-agent-sa`: Granted **read-only** trace/log roles (`roles/cloudtrace.user`, `roles/logging.viewer`).
* Building the containers with Cloud Build and deploying both to Cloud Run.

---

## ⚙️ The Antigravity Ecosystem Three Pillars

This codebase highlights how to interact with the SRE agent using different interfaces in the Antigravity ecosystem:

### 1. The Antigravity SDK
Used programmatically in [config.py](file:///home/xsavikx/AntigravityProjects/sre-agent/agent/src/agent/config.py) to configure the agent's behavior. We specify `LocalAgentConfig` with system instructions, register custom python tools, and declare safety policies (e.g. denying all write operations by default and requiring human confirmation for terminal commands).

### 2. The Antigravity CLI
Useful for developers to manage and inspect skills from the command line. You can load this workspace skill directly using:
```bash
antigravity skills list
antigravity run agent/config.py --prompt "Diagnose recent latency spikes"
```

### 3. Antigravity 2.0 (Visual Workspace)
The visual desktop application automatically discovers skills placed in the `skills/` directory. When you open this repository in the Antigravity 2.0 Desktop app, the `sre_incident_solver` skill is immediately recognized via [SKILL.md](file:///home/xsavikx/AntigravityProjects/sre-agent/skills/sre_incident_solver/SKILL.md) and added to your visual canvas, allowing you to run and audit SRE tasks in a graphical dashboard.

---

## 🧹 Tearing Down the Stack
To prevent any ongoing billing charges in your GCP project, run the cleanup script to remove all deployed Cloud Run services, IAM bindings, and service accounts:
```bash
./cleanup.sh
```
