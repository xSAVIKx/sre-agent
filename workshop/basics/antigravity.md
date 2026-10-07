# Antigravity SDK basics: run an agent with safe limits

**In one sentence:** the [Antigravity SDK](https://antigravity.google/docs) runs the agent
harness of Google Antigravity in your own program: the model, the tools, the conversation and,
most important here, **policies** and **hooks** that control what the agent can do.

**The problem it solves:** a capable agent can read files, run commands and get URLs. That is
useful in a coding tool, and dangerous in an on-call chat that anyone can use. The SDK lets you
say which tool calls are allowed. The runtime enforces this. The model cannot change it.

## The key elements

### 1. Agent and configuration

`Agent` runs a conversation. The configuration gives it its instructions, tools, policies and
hooks. From `agent/src/agent/config.py` and `agent/src/agent/routes.py`:

```python
config = LocalAgentConfig(
    system_instructions=SYSTEM_INSTRUCTIONS, tools=tools, policies=safety_policies, hooks=[SreToolErrorHook()]
)
async with Agent(config) as agent:
    response = await agent.chat(prompt)
    async for chunk in response.chunks:  # Thought, ToolCall, Text ...
        ...
```

### 2. Built-in tools and custom tools

An Antigravity agent has **built-in tools**, for example `run_command`, `view_file`,
`read_url_content` and `search_web`. You add **custom tools**: Python functions with type hints
and docstrings. The Orchestrator has three custom tools, one for each skill of the SRE agent:
`list_incidents`, `diagnose_sre` and `write_post_mortem`.

### 3. Policies

A policy decides each tool call before it runs: `allow`, `deny` or `ask_user`.

```python
from google.antigravity.hooks.policy import allow, ask_user, deny

[deny("*"), allow("list_incidents"), allow("diagnose_sre"), allow("write_post_mortem")]
```

The precedence rules:

* A rule for a specified tool has priority over the wildcard rule (`"*"`).
* `deny` has priority over `ask_user`, and `ask_user` has priority over `allow`.
* With no matching rule, the call is denied: a missing rule fails closed.

`deny("*")` also blocks the built-in tools that you did not register. An instruction in the
system prompt is only a request to the model. A policy is enforced.

### 4. Ask the user

`ask_user("tool", handler=...)` stops the call and asks a person. The handler receives the tool
call, and returns `True` to approve it. Use it for actions that change things, for example a
restart.

### 5. Hooks

A hook runs your code at a point of the agent loop. `SreToolErrorHook` runs when a tool fails,
and gives the model a clear error message, so it can answer the user correctly.

## Try it

```bash
uv run workshop/basics/try_policy.py
uv run workshop/basics/try_policy.py run_command my_new_tool
```

The script shows the decision of the Orchestrator's policy for each tool:

```text
The policy: deny("*"), allow("list_incidents"), allow("diagnose_sre"), allow("write_post_mortem")

  list_incidents       ✅ allow
  run_command          ⛔ deny
```

Before step 4, the policy is only `deny("*")`, and all tools show `⛔ deny`.

## In the workshop

* **Step 4** adds the three `allow(...)` rules.
* **Stretch (step 4)** controls a new tool with `ask_user`.
