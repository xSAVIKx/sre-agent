# Step 5 · Show the severity (14 min)

**Goal:** the chat UI shows a red **SEV1** badge on top of the diagnosis and the post-mortem.

## The idea: agents send UI, not HTML

The SRE engine answers in Markdown for the model and in **[A2UI](https://a2ui.org)** for people.
A2UI (v0.9) describes a UI as JSON that names components from a **catalog**. The agent never sends
HTML or code, and the browser decides how each component looks:

```json
{"version": "v0.9", "createSurface": {"surfaceId": "sre-1a2b", "catalogId": "…/catalogs/sre/v1/catalog.json"}}
{"version": "v0.9", "updateComponents": {"surfaceId": "sre-1a2b", "components": [
  {"id": "root", "component": "Card", "child": "body"},
  {"id": "body", "component": "Column", "children": ["severity", "title"]},
  {"id": "severity", "component": "SeverityBadge", "level": "SEV1", "contribution": 99.3},
  {"id": "title", "component": "Text", "text": "🔬 Incident diagnosis", "variant": "h3"}
]}}
```

* **Where it's built:** `sre_agent/src/sre_agent/a2ui_surfaces.py` builds one surface per result:
  the incident list, the diagnosis, and the post-mortem.
* **How it travels:** each message is an A2A data part marked `application/json+a2ui`. The SRE agent
  only sends them to callers that declare A2UI client capabilities, and the Orchestrator does that
  for its chat.
* **How it's drawn:** the browser renders the surface with `@a2ui/lit`
  (`agent/web/src/sre-a2ui.js`, prebuilt into `agent/src/agent/static/sre-a2ui.js`).
* **The catalog:** the SRE catalog is A2UI's basic catalog (Card, Column, List, Tabs, Text,
  Button…) plus two custom components, `SeverityBadge` and `Download`. Each has a schema on both
  sides: a Pydantic model in Python, a Zod schema in the browser.

Severity comes from how much of the request one span owns (the bottleneck from step 1):

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

1. `classify_severity(contribution)`: return the level for a bottleneck share.
2. `_badge(bottleneck_share)`: return the `SeverityBadge` component, or `[]` when there's no
   bottleneck. The diagnosis and post-mortem cards put whatever you return first.

## Run it

```bash
uv run workshop/check.py 5

uv run simulate_incident.py                                 # fresh incident telemetry
MOCK_GCP=true uv run uvicorn agent.main:app --port 8080     # the web chat
```

Open <http://localhost:8080/chat> and ask **"What are the latest failures?"**. You should see:

1. an **incident list** surface. Its rows come from the surface's data model through a List
   template, and each row has **Diagnose** and **Post-mortem** buttons;
2. click **Diagnose**: the button sends an A2UI action, and the Orchestrator turns it into the next
   chat turn. That turn still goes through its policy (step 4);
3. a diagnosis card with a red `SEV1 · bottleneck owns 99.3% of the request` badge, the report in
   tabs, and a **Download report** button.

Then ask "hello": no tool call, no surface. The Orchestrator only delegates when it needs to.

## Discuss

* Why JSON components instead of HTML? The agent can't inject scripts or styles. The client
  renders only what its catalog allows and themes it like the rest of the app.
* The browser's renderer validates every component against its schema. So does `a2ui-core` in
  `sre_agent/test/test_a2ui_surfaces.py`, which makes a malformed surface fail CI rather than the
  demo.

## Stretch

* Run `uv run workshop/check.py all`: everything should be green ✅.
* Change the thresholds and watch the badge change color.
* Add a third custom component. Give it a Pydantic model in `a2ui_surfaces.py` and a Lit element in
  `agent/web/src/sre-a2ui.js`, then rebuild with `cd agent/web && npm ci && npm run build`.
* Have Docker? `docker compose up --build` runs the same UI with the Orchestrator, the SRE engine
  and the Inventory agent as **separate services** talking A2A over HTTP. Trigger an incident with
  `curl "http://localhost:8081/api/gateway?trigger_error=true"`.

**Stuck?** `git apply workshop/steps/05-show-the-severity/solution.patch`
