# Step 6 · Wrap-up: production and next steps (10 min)

You've built the whole loop: **instrumented app → tools → ADK multi-agent engine → policy-gated
Orchestrator → rich chat UI**. Here's how it goes to production, and where to take it next.

## Production on Cloud Run (facilitator demo)

```bash
./bootstrap.sh   # pick/create a project, link billing, store GEMINI_API_KEY in .env
./deploy.sh      # APIs, least-privilege service accounts, 4 Cloud Run services (~6 min)
./cleanup.sh     # delete everything again
```

The interesting part is the IAM split:

| Service account       | Can                                   | Cannot                  |
|:----------------------|:--------------------------------------|:------------------------|
| `sre-chaos-monkey-sa` | **write** traces and logs             | read any telemetry      |
| `sre-agent-sa`        | **read** traces, logs, metrics (+ write its own spans) | change telemetry or infrastructure |

The app that *creates* incidents can't read telemetry, and the agent that *investigates* them
can't change anything; it only adds its own trace spans, so it can diagnose itself too. Least
privilege at the IAM layer, plus the policy layer from step 4.

> ⚠️ The demo deploys the services with `--allow-unauthenticated` so the chat is easy to reach.
> Tear it down after the session (`./cleanup.sh`) or put it behind IAP before sharing the URL.

## Take it home: the Antigravity skill

`skills/sre_incident_solver/` packages the same SRE engine as an **Antigravity Agent Skill**
(`SKILL.md` + code). It's generated from `sre_agent/` by `scripts/sync_skill.py`, so it never
drifts from what you just built. Load it in your own Antigravity agent with the SDK's
`skills_paths` option.

## Keep going

[`EXERCISES.md`](../../../EXERCISES.md) has graded follow-ups: log-pattern clustering, an SLO /
error-budget section, a Mitigation Planner agent, multi-trace correlation, human-in-the-loop
remediation, Slack notifications, RAG over runbooks, an eval harness, and the fully autonomous
alert → diagnosis → post-mortem capstone.
