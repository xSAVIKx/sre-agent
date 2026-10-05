# Step 4 · Lock the Orchestrator down (12 min)

**Goal:** Let the Orchestrator do only one type of task: send requests to the SRE agent.

## The idea: deny by default, then allow specified tools

The Orchestrator (`agent/src/agent/config.py`) is the agent that users talk to. It uses the
**Antigravity SDK**. Agents from this SDK have powerful built-in tools: they can read files, run
commands and get URLs. An on-call chat agent does not need these tools.

The Antigravity runtime applies **policies** before a tool call runs. The model cannot change a
policy decision:

```python
from google.antigravity.hooks.policy import allow, ask_user, deny

[deny("*"), allow("diagnose_sre")]
```

The precedence rules are:

* A rule for a specified tool has priority over the wildcard rule (`"*"`).
* `deny` has priority over `ask_user`, and `ask_user` has priority over `allow`.

Thus, `deny("*")` with `allow("diagnose_sre")` means "no tools, except `diagnose_sre`".

The SRE agent publishes its A2A agent card at `/.well-known/agent-card.json`. The Orchestrator has
one tool for each **skill** on this card:

| Orchestrator tool | Function |
|:--|:--|
| `list_incidents` | Lists the current failures. It is fast. |
| `diagnose_sre` | Finds the root cause. It calls the `diagnose_incident` skill. |
| `write_post_mortem` | Writes the post-mortem. |

The model selects the least expensive tool for the question. Each tool needs its own `allow`. If
you forget a tool, the policy denies it. The policy fails closed.

At this time, the policy is `[deny("*")]`. This policy is safe, but the Orchestrator cannot send
requests to the SRE agent. You saw this result in step 0:

```text
The `diagnose_sre` tool call was blocked by the safety policy (decision: deny).
```

## Your task

1. Find the TODO:

   ```bash
   git grep -n "TODO(step-4)"
   ```

2. In `build_safety_policies()`, keep `deny("*")`.
3. Add one `allow(...)` for each of the three SRE tools.

## Run it

```bash
uv run workshop/check.py 4
uv run simulate_incident.py          # the real path, through the Orchestrator - no --engine-only
```

You get the full **AGENT DIAGNOSIS REPORT** through the Orchestrator.

The check also runs a contract test against the *real* Antigravity SDK. The test proves that the
SDK harness receives your policy as
`{"*": DENY, "list_incidents": ALLOW, "diagnose_sre": ALLOW, "write_post_mortem": ALLOW}`.

## Discuss

* Why use `deny("*")` + `allow(...)`, and not only register fewer tools? The built-in tools of the
  SDK are available also when you do not register them. The runtime enforces a policy. An
  instruction in the system prompt is only a request.
* There is no other path: `/chat` sends **each** message through this agent. Thus, all diagnostics
  go through the allowed tools.
* Why one tool for each skill, and not one generic `call_sre_agent(skill=...)` tool? Each tool gets
  its own policy decision. For example, you can `ask_user` before a post-mortem, and always allow
  the incident list.

## Stretch: human in the loop

1. Add a mock tool `restart_service(service_name: str) -> str`.
2. Control the tool with `ask_user("restart_service", handler=...)`.
3. Write a handler that logs the request and returns `False`.

The handler receives the pending `ToolCall`. It returns `True` to approve the call. This task is
Exercise 7 in [`EXERCISES.md`](../../../EXERCISES.md).

**Stuck?** Run `uv run workshop/step.py hint 4`, or ask the workshop coach in Antigravity. To apply the
solution: `uv run workshop/step.py solve 4`.
