# Step 5 · Show the severity (14 min)

**Goal:** the chat UI shows a red **SEV1** badge on top of the post-mortem.

## The idea: agents speak Markdown, UIs want components

The SRE engine returns Markdown. The Orchestrator's `translate_markdown_to_a2ui()`
(`agent/src/agent/a2ui_translator.py`) turns it into **A2UI** components, small JSON objects the
browser knows how to draw (`alert`, `section`, `download_button`…). The browser side
(`agent/src/agent/index.html`) already renders a `severity_badge` component. Your job is to emit
one.

Severity comes from how much of the request one span owns. The cascade report from step 1 says:

```text
*   **Self-Execution Time**: `10200 ms` (99.3% of total trace)
```

| Bottleneck share | Level |
|:-----------------|:------|
| ≥ 90 %           | SEV1  |
| ≥ 50 %           | SEV2  |
| otherwise        | SEV3  |

(`SEVERITY_THRESHOLDS` already encodes this table.)

## Your task

```bash
git grep -n "TODO(step-5)"
```

Implement `classify_severity(text)` and return
`{"type": "severity_badge", "level": "SEV1", "contribution": 99.3}`, or `None` when there's no
bottleneck figure. `translate_markdown_to_a2ui` already puts your badge first.

## Run it

```bash
uv run workshop/check.py 5

uv run simulate_incident.py                                 # fresh incident telemetry
MOCK_GCP=true uv run uvicorn agent.main:app --port 8080     # the web chat
```

Open <http://localhost:8080/chat> and ask **"Diagnose the recent latency spikes"**. You should see:

1. live progress while the Orchestrator calls `diagnose_sre`,
2. a red `SEV1 · bottleneck owns 99.3% of the request` badge,
3. the post-mortem preview and a **Download Post-Mortem Markdown** button.

Then ask "hello": no tool call, no badge. The Orchestrator only delegates when it needs to.

## Stretch

* Run `uv run workshop/check.py all`: everything should be green ✅.
* Change the thresholds and watch the badge change color.
* Have Docker? `docker compose up --build` runs the same UI with the Orchestrator, the SRE engine
  and the Inventory agent as **separate services** talking A2A over HTTP. Trigger an incident with
  `curl "http://localhost:8081/api/gateway?trigger_error=true"`.

**Stuck?** `git apply workshop/steps/05-show-the-severity/solution.patch`
