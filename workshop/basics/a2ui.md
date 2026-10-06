# A2UI basics: agents that send UI

**In one sentence:** [A2UI](https://a2ui.org) is an open format that lets an agent send a user
interface as JSON: the agent names components from a catalog, and the client draws them with its
own code and style.

**The problem it solves:** text is a bad interface for many results: a list of incidents, a
report with tabs, a button to start the next action. HTML from an agent is dangerous: it can
contain scripts. A2UI sends only data. The client decides which components it allows and how
they look.

## The key elements

### 1. Surface

A surface is one piece of UI from the agent, for example one card. A surface comes as a list of
**messages**:

| Message | Function |
|:--|:--|
| `createSurface` | Starts a surface, and names its catalog. |
| `updateComponents` | Sends or changes components. |
| `updateDataModel` | Sends or changes the data that the components show. |

### 2. Catalog

The catalog is the list of components that the client can draw. The agent can use only those.
This project uses the A2UI **basic catalog** (Card, Column, Row, List, Tabs, Text, Button…) plus
two custom components: `SeverityBadge` and `Download`.

### 3. Components

Components are a **flat list**. Each component has an `id` and a `component` type, and refers to
its children by their IDs. The component with the ID `root` is the top.

```json
{"id": "root", "component": "Card", "child": "body"},
{"id": "body", "component": "Column", "children": ["severity", "title"]},
{"id": "severity", "component": "SeverityBadge", "level": "SEV1", "contribution": 99.3},
{"id": "title", "component": "Text", "text": "🔬 Incident diagnosis", "variant": "h2"}
```

A flat list is easy for a model to write and easy to change: an update replaces components by ID.

### 4. Data model and binding

A value can be fixed, or a **binding** to the data model: `{"path": "/status"}`. A `List`
repeats a template for each item of an array. Inside the template, a relative path reads a field
of that item:

```json
{"id": "incidents", "component": "List", "children": {"componentId": "row", "path": "/incidents"}},
{"id": "summary", "component": "Text", "text": {"path": "summary"}}
```

When the data changes (`updateDataModel`), the UI shows the new values. The components do not
change.

### 5. Actions

A button has an **action**:

* An **event** goes to the agent: a name and a context.
  `{"event": {"name": "diagnose_incident", "context": {"traceId": {"path": "traceId"}}}}`
* A **function call** runs in the browser and does not go to the agent:
  `{"functionCall": {"call": "openUrl", "args": {"url": "…"}}}`

### 6. Negotiation

The client says which catalogs it can draw. Over A2A, the request has the metadata
`a2uiClientCapabilities` and activates the A2UI extension. The SRE agent sends surfaces only to
such clients. Other clients get only text.

### 7. Renderer

The renderer draws the surface in the client. The chat uses `@a2ui/lit`, from a prebuilt bundle
(`agent/src/agent/static/sre-a2ui.js`). It checks each component against the catalog schema.

## Try it: the playground

1. Start the chat: `uv run workshop/chat.py`.
2. Open <http://localhost:8080/playground>.
3. Select an example. Change the JSON, then press **Render** (or Ctrl+Enter).

Things to try:

* Example 1: change the text, and add a second button.
* Example 2: add a third incident to the data model. The components do not change.
* Example 2: click **Diagnose** on each row. The action log shows the trace ID of that row.
* Example 3: change `SEV1` to `SEV3`, and `99.3` to `40`.
* Change a component to `"Blink"`. The renderer rejects it: it is not in the catalog.

## In the workshop

* **Step 5** builds the `SeverityBadge` component and the button's event context.
* **Stretch (step 5)** changes a surface with `updateDataModel` only.
