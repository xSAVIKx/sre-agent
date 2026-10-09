# A2A basics: agents that talk to agents

**In one sentence:** the [Agent2Agent (A2A) protocol](https://a2a-protocol.org) is an open
standard that lets an agent find another agent, send it work, and get the result, also when the
two agents use different frameworks.

**The problem it solves:** agents are often separate programs, from separate teams, in separate
languages. Without a standard, each pair of agents needs its own API. With A2A, each agent
publishes what it can do, and each client uses the same protocol: JSON-RPC over HTTP, with a
stream of updates.

## The key elements

### 1. Agent card

Each agent publishes a JSON document at `/.well-known/agent-card.json`. It says who the agent
is, where to send requests, what the agent can do, and which protocol features it supports.
`build_agent_card()` in `sre_agent/src/sre_agent/a2a_agent.py` makes the card of the SRE agent.

### 2. Skill

A skill is one thing that the agent can do. The **description** is for other agents and their
models: write it like a tool docstring.

```python
AgentSkill(
    id=LIST_INCIDENTS,
    name="List recent incidents",
    description="Lists the recent failing and slow requests in Cloud Trace, most important first, ...",
    examples=["What are the latest failures?"],
)
```

### 3. Message and parts

A client sends a **message**. A message has **parts**: text, structured data or files. Each
part can have metadata, for example a media type. The SRE agent sends its result as three types
of parts: Markdown text, the data, and A2UI messages (`application/json+a2ui`).

### 4. Task

Each request starts a **task**. The task has a state: `SUBMITTED`, `WORKING`, then `COMPLETED`
or `FAILED`. With streaming, the client gets a **status update** for each change, and the result
as an **artifact**.

### 5. Context ID

The **context ID** groups the messages of one conversation. The chat sends its conversation ID,
so the SRE agent can connect the requests of one chat.

### 6. Metadata and extensions

The request **metadata** carries more parameters. This project uses it to select the skill:
`{"skill": "diagnose_incident", "trace_id": "…"}`. An **extension** is an agreed addition to the
protocol, named by a URI. The agent lists its extensions on the card, and the client activates
one on its message. A2UI over A2A is an extension.

### 7. Server and client

* **Server:** ADK's `to_a2a(agent, agent_card=card)` serves the SRE agent. Without ADK, an
  `a2a-sdk` `AgentExecutor` serves an agent (see `inventory_agent/`).
* **Client:** `call_agent()` in `sre_common/src/sre_common/a2a_client.py` reads the card,
  sends one message, forwards the progress, and returns the result.

## Try it

```bash
uv run workshop/basics/try_a2a.py          # the list_incidents skill
uv run workshop/basics/try_a2a.py --ui     # also ask for A2UI
```

The script uses plain HTTP and JSON, so you see the protocol itself. Look for these items in the
output:

1. The **agent card** and its skills: `list_incidents` and `diagnose_incident`. Step 3 adds
   `write_post_mortem`.
2. The **request**: a JSON-RPC `SendStreamingMessage` with the header `A2A-Version: 1.0`, a
   message, and the metadata `skill`.
3. The **stream**: the task (`SUBMITTED`), status updates (`WORKING`, with progress text), the
   **artifact** with text, data and A2UI parts, and at the end `COMPLETED`. Each A2UI part gets
   one line: its media type, `application/json+a2ui`, and the message: `createSurface`,
   `updateComponents` (with the component types) or `updateDataModel`.
4. With `--ui`, at the end: the **A2UI messages** of the result, in full. Paste them into the
   [playground](a2ui.md#try-it-the-playground) to see the UI.

Add `--raw` to see the full JSON of each event. Try `diagnose_incident` as the skill. If the
script says that there is no incident yet, run `uv run simulate_incident.py --engine-only` first.

## In the workshop

* **Step 3** publishes a new skill on the card (the server side).
* **Step 4** calls the SRE agent with `call_agent()` (the client side).
