# Step 6 · Wrap-up: production and next steps (10 min)

**Goal:** Learn how the system goes to production, and what you can do next.

You built the full loop: **instrumented app → tools → ADK multi-agent SRE agent → Orchestrator
with a policy → chat UI with A2UI surfaces**.

## Production on Cloud Run (facilitator demo)

```bash
./bootstrap.sh   # pick/create a project, link billing, store GEMINI_API_KEY in .env
./deploy.sh      # APIs, least-privilege service accounts, 4 Cloud Run services (~6 min)
./cleanup.sh     # delete everything again
```

The important part is the IAM split:

| Service account       | Can                                   | Cannot                  |
|:----------------------|:--------------------------------------|:------------------------|
| `sre-chaos-monkey-sa` (target app) | **Write** traces and logs | Read telemetry |
| `sre-agent-sa` (SRE agent and Orchestrator) | **Read** traces, logs and metrics. Write its own trace spans. Use Firestore. Read the `GEMINI_API_KEY` secret. | Change telemetry or infrastructure |

The target app *makes* incidents, but it cannot read telemetry. The SRE agent *investigates*
incidents, but it cannot change telemetry or infrastructure. It only writes its own trace spans.
Thus, the SRE agent can also diagnose its own requests.

This is least privilege at the IAM layer. The policy from step 4 adds least privilege at the agent
layer.

> ⚠️ The demo deploys the services with `--allow-unauthenticated`. This makes the chat easy to
> open. After the session, delete the deployment (`./cleanup.sh`). Alternatively, put the
> services behind IAP before you share the URL.

## Use the Antigravity skill

`.agents/skills/sre_incident_solver/` contains the same SRE agent as an **Antigravity Agent
Skill** (`SKILL.md` + code). `scripts/sync_skill.py` generates the skill from `sre_agent/`. Thus,
the skill always agrees with the code that you built.

Antigravity finds the skills in `.agents/skills/` when you open this repository:

1. Run `uv run simulate_incident.py --engine-only`. It makes an incident.
2. Open the repository in the Antigravity app, or run `agy` in it.
3. Ask: "Use the sre_incident_solver skill to diagnose the latest incident."

The agent reads `SKILL.md` and runs the diagnosis (it asks before it runs a command). This is a third way to use the same engine:
in the chat (A2A), in a terminal (`simulate_incident.py`), and as a skill of a coding agent.

The same folder has the workshop skills: `sre-agent-setup`, `sre-workshop-coach` and
`sre-agent-deploy`.

## Next steps

[`EXERCISES.md`](../../../EXERCISES.md) contains follow-up exercises in three levels:

| Level | Exercises |
|:--|:--|
| 1: Tools and reports | Make the metrics show the truth, log-pattern clustering tool, SLO and error-budget section, severity in the post-mortem |
| 2: Workflow and agents | Mitigation Planner agent, multi-trace incident correlation, human-in-the-loop remediation tool, notification to Slack or a webhook |
| 3: Architecture and production | RAG over runbooks, live on real GCP, evaluation harness |
| Capstone | The fully autonomous loop: alert → diagnosis → post-mortem |
