# Step 5 · Show the severity (14 min)

**Goal:** Make the chat UI show a red **SEV1** badge at the top of the diagnosis and the
post-mortem.

## The idea: agents send UI, not HTML

The SRE agent sends Markdown for the model and **[A2UI](https://a2ui.org)** for people.

A2UI (v0.9) describes a UI as JSON. The JSON names components from a **catalog**. The agent never
sends HTML or code. The browser decides how each component looks:

```json
{"version": "v0.9", "createSurface": {"surfaceId": "sre-1a2b", "catalogId": "…/catalogs/sre/v1/catalog.json"}}
{"version": "v0.9", "updateComponents": {"surfaceId": "sre-1a2b", "components": [
  {"id": "root", "component": "Card", "child": "body"},
  {"id": "body", "component": "Column", "children": ["severity", "title"]},
  {"id": "severity", "component": "SeverityBadge", "level": "SEV1", "contribution": 99.3},
  {"id": "title", "component": "Text", "text": "🔬 Incident diagnosis", "variant": "h2"}
]}}
```

| Topic | Details |
|:--|:--|
| Where the SRE agent makes the surfaces | `sre_agent/src/sre_agent/a2ui_surfaces.py` makes one surface for each result: the incident list, the diagnosis and the post-mortem. |
| How a surface moves | Each message is an A2A data part with the type `application/json+a2ui`. The SRE agent sends these parts only to callers that declare A2UI client capabilities. The Orchestrator declares them for its chat. |
| How the browser shows a surface | The browser uses `@a2ui/lit` (`agent/web/src/sre-a2ui.js`, prebuilt into `agent/src/agent/static/sre-a2ui.js`). |
| The catalog | The SRE catalog is the A2UI basic catalog (Card, Column, List, Tabs, Text, Button…) plus two custom components: `SeverityBadge` and `Download`. Each custom component has a schema on the two sides: a Pydantic model in Python and a Zod schema in the browser. |

The severity comes from the share of the request time that one span owns. This span is the
bottleneck from step 1:

| Bottleneck share | Level |
|:-----------------|:------|
| ≥ 90 %           | SEV1  |
| ≥ 50 %           | SEV2  |
| otherwise        | SEV3  |

`SEVERITY_THRESHOLDS` already contains this table.

## Your task

1. Find the TODOs:

   ```bash
   git grep -n "TODO(step-5)"
   ```

2. In `classify_severity(contribution)`, return the level for a bottleneck share.
3. In `_badge(bottleneck_share)`, return the `SeverityBadge` component.
4. In `_badge(bottleneck_share)`, return `[]` when there is no bottleneck.

The diagnosis card and the post-mortem card put the return value of `_badge` first.

## Run it

1. Do these commands:

   ```bash
   uv run workshop/check.py 5

   uv run simulate_incident.py                                 # fresh incident telemetry
   uv run workshop/chat.py                                     # the web chat
   ```

2. Open <http://localhost:8080/chat>.
3. Ask **"What are the latest failures?"**. You see an **incident list** surface.
4. Click **Diagnose** on a row. You see a diagnosis card.
5. Make sure that the diagnosis card shows the red `SEV1 · bottleneck owns 99.3% of the request`
   badge, the report in tabs, and a **Download** button.
6. Ask "hello". The Orchestrator calls no tool and shows no surface.

About the incident list:

* The rows come from the data model of the surface through a List template.
* Each row has a **Diagnose** button and a **Post-mortem** button.
* A button sends an A2UI action. The Orchestrator changes the action into the next chat turn.
* This chat turn also goes through the policy from step 4.

The Orchestrator sends a request to the SRE agent only when it is necessary.

## Discuss

* Why use JSON components, and not HTML? The agent cannot inject scripts or styles. The client
  shows only the components that its catalog allows. The client applies the same theme as the
  rest of the app.
* The renderer in the browser validates each component against its schema.
  `sre_agent/test/test_a2ui_surfaces.py` does the same check with `a2ui-core`. Thus, an incorrect
  surface fails in CI, not in the demo.

## Stretch

* Run `uv run workshop/check.py all`. All steps must be green ✅.
* Change the thresholds. Look at the change in the badge color.
* Add a third custom component:
  1. Add a Pydantic model in `a2ui_surfaces.py`.
  2. Add a Lit element in `agent/web/src/sre-a2ui.js`.
  3. Build the UI again with `cd agent/web && npm ci && npm run build`.
* If you have Docker, run `docker compose up --build`. This command runs the same UI. The
  Orchestrator, the SRE agent and the Inventory agent run as **separate services**. They talk A2A
  over HTTP. To make an incident, run
  `curl "http://localhost:8081/api/gateway?trigger_error=true"`.

**Stuck?** `git apply workshop/steps/05-show-the-severity/solution.patch`
