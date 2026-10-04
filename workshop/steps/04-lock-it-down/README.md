# Step 4 · Lock the Orchestrator down (12 min)

**Goal:** the user-facing Orchestrator can do exactly one thing, delegate to the SRE engine, and
nothing else.

## The idea: deny by default, allow deliberately

The Orchestrator (`agent/src/agent/config.py`) is the agent your users chat with. It's built with
the **Antigravity SDK**, whose agents come with powerful built-in tools (reading files, running
commands, fetching URLs). An on-call chatbot needs none of them.

Antigravity enforces **policies** in its runtime, before a tool call executes. The model can't
talk its way past them:

```python
from google.antigravity.hooks.policy import allow, ask_user, deny

[deny("*"), allow("diagnose_sre")]
```

Precedence is *specific beats wildcard* and *deny beats ask beats allow*, so `deny("*")` +
`allow("diagnose_sre")` means "nothing, except this one tool".

The Orchestrator has one tool per **skill** on the SRE agent's A2A card
(`/.well-known/agent-card.json`): `list_incidents` (what is failing - fast), `diagnose_sre`
(root cause, calls the `diagnose_incident` skill) and `write_post_mortem`. The model picks the
cheapest tool for the question; each one needs its own `allow`. A tool you forget stays denied:
the policy fails closed.

Right now the policy is `[deny("*")]`. It's safe, but useless: the Orchestrator can't even
delegate. You saw this in step 0:

```text
The `diagnose_sre` tool call was blocked by the safety policy (decision: deny).
```

## Your task

```bash
git grep -n "TODO(step-4)"
```

Complete `build_safety_policies()`, keeping `deny("*")` and allowing the three SRE tools.

## Run it

```bash
uv run workshop/check.py 4
uv run simulate_incident.py          # the real path, through the Orchestrator - no --engine-only
```

You now get the full **AGENT DIAGNOSIS REPORT** through the Orchestrator. The check also runs a
contract test against the *real* Antigravity SDK, proving your policy reaches its harness as
`{"*": DENY, "list_incidents": ALLOW, "diagnose_sre": ALLOW, "write_post_mortem": ALLOW}`.

## Discuss

* Why `deny("*")` + `allow(...)` instead of just not registering other tools? The SDK's built-in
  tools exist whether you register them or not. A policy is enforced by the runtime; an
  instruction in the system prompt is only a request.
* There is no back door: `/chat` sends **every** message through this agent, so diagnostics only
  ever happen through the allowed tools.
* Why one tool per skill instead of a single generic `call_sre_agent(skill=...)`? Each tool is a
  separate policy decision: you could `ask_user` before a post-mortem but allow listing freely.

## Stretch: human in the loop

Add a (mock) `restart_service(service_name: str) -> str` tool and gate it with
`ask_user("restart_service", handler=...)`. The handler receives the pending `ToolCall` and
returns `True` to approve. Start with one that logs the request and returns `False`. This is
Exercise 7 in [`EXERCISES.md`](../../../EXERCISES.md).

**Stuck?** `git apply workshop/steps/04-lock-it-down/solution.patch`
