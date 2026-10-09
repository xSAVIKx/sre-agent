# Step 4 · Call the agent over A2A, safely (12 min)

**Goal:** Make the Orchestrator call the SRE agent over A2A, and let it do nothing else.

**Builds on:** [A2A basics](../../basics/a2a.md): 4. Task, 6. Metadata and extensions, 7. Client. [Antigravity SDK basics](../../basics/antigravity.md): 3. Policies.

## The idea, part 1: an A2A client

The Orchestrator (`agent/src/agent/config.py`) is the agent that users talk to. It has one tool
for each skill on the SRE agent's card:

| Orchestrator tool | SRE agent skill |
|:--|:--|
| `list_incidents` | `list_incidents` (fast) |
| `diagnose_sre` | `diagnose_incident` |
| `write_post_mortem` | `write_post_mortem` (step 3) |

All three tools use `_call_sre_skill`, which calls `call_agent(...)` from `sre_common`.
`call_agent` reads the agent card, sends one A2A message, forwards the progress updates, and
returns the result.

**Why the TODO has A2UI lines.** The SRE agent sends UI (A2UI surfaces, step 5) only to a caller
that asks for it, and a chat asks in its A2A request. These three lines of the TODO are the client
side of A2UI:

* `metadata["a2uiClientCapabilities"] = A2UI_CLIENT_CAPABILITIES`: the components that the chat
  can draw (its catalog).
* `extensions=[A2UI_EXTENSION_URI]`: turns on the A2UI extension for this request.
* `sink.a2ui = result.a2ui`: keeps the surface that comes back, so that the chat can show it.

They are here because they are part of the A2A request. Without them, the chat shows only text.
Step 5 explains A2UI, and changes the other side: the surfaces that the SRE agent builds.

On your laptop, there is no SRE agent service. The Orchestrator starts the SRE agent on a free
local port (`agent/src/agent/local_sre.py`). The calls are real A2A over HTTP, as in production.

## The idea, part 2: deny by default

The Orchestrator uses the **Antigravity SDK**. Its agents have powerful built-in tools: they can
read files, run commands and get URLs. An on-call chat agent does not need these tools.

The Antigravity runtime applies **policies** before each tool call. The model cannot change a
policy decision:

```python
from google.antigravity.hooks.policy import allow, ask_user, deny

[deny("*"), allow("diagnose_sre")]  # no tools, except diagnose_sre
```

* A rule for a specified tool has priority over the wildcard rule (`"*"`).
* `deny` has priority over `ask_user`, and `ask_user` has priority over `allow`.

At this time, the policy is `[deny("*")]`. You saw the result in step 0:

```text
The `diagnose_sre` tool call was blocked by the safety policy (decision: deny).
```

## Your task

1. Find the TODOs:

   ```bash
   git grep -n "TODO(step-4)"
   ```

2. In `_call_sre_skill`, replace the `raise` with the A2A call. The TODO lists the three parts:
   the A2UI capabilities, `call_agent(...)`, and the result.
3. In `build_safety_policies()`, keep `deny("*")`, and add one `allow(...)` for each of the three
   SRE tools.

## Run it

1. Do these commands:

   ```bash
   uv run workshop/check.py 4
   uv run simulate_incident.py          # the full path, through the Orchestrator: no --engine-only
   ```

2. Find these lines in the log. The Orchestrator starts the SRE agent and calls it over A2A:

   ```text
   Started the SRE agent locally for A2A calls: http://127.0.0.1:...
   Calling the SRE agent's diagnose_incident skill over A2A: http://127.0.0.1:...
   ```

3. Start the chat with `uv run workshop/chat.py`, and open <http://localhost:8080/chat>.
4. Ask "What are the latest failures?". Then ask for a post-mortem: your skill from step 3.

The check also runs a contract test against the *real* Antigravity SDK. The test proves that the
SDK receives your policy as
`{"*": DENY, "list_incidents": ALLOW, "diagnose_sre": ALLOW, "write_post_mortem": ALLOW}`.

## Discuss

* Why `deny("*")` + `allow(...)`, and not only fewer tools? The built-in tools of the SDK are
  available also when you do not register them. The runtime enforces a policy. An instruction in
  the system prompt is only a request.
* Why one Orchestrator tool for each skill, and not one generic `call_sre_agent(skill=...)`? Each
  tool gets its own policy decision. For example, you can `ask_user` before a post-mortem, and
  always allow the incident list.
* The agent card already lists the skills. Why does the Orchestrator still define its own tools?
  A skill on a card has no parameter schema: the tools give the model typed arguments, such as
  `trace_id`. And the Orchestrator decides what it may do, not the remote agent: a new skill on
  the card must not give the Orchestrator a new power without a review. You saw this: step 3 put
  `write_post_mortem` on the card, and the chat can use it only after this step.

## Stretch: human in the loop

1. Add a mock tool `restart_service(service_name: str) -> str`.
2. Control the tool with `ask_user("restart_service", handler=...)`.
3. Write a handler that logs the request and returns `False`.

This task is Exercise 7 in [`EXERCISES.md`](../../../EXERCISES.md).

**Stuck?** Run `uv run workshop/step.py hint 4`, or ask the workshop coach in Antigravity. To apply
the solution: `uv run workshop/step.py solve 4`.
