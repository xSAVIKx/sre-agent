# Step 1 · Find the bottleneck (12 min)

**Goal:** make `analyze_trace_cascade` name the span that is *actually* slow.

## The idea: inclusive vs. exclusive time

A trace is a tree of spans. Each span's **inclusive** time is its wall-clock duration *including*
its children. The gateway span lasts 10 270 ms, but almost all of that is spent waiting on the
backend, which is waiting on the database.

A span's **exclusive** (self) time is the part no child accounts for:

```
exclusive = inclusive − time covered by its children
```

```
/api/gateway   ██████████████████████████████  10 270 ms inclusive →    20 ms self
 /api/backend   █████████████████████████████  10 250 ms inclusive →    50 ms self
  /api/database  ████████████████████████████  10 200 ms inclusive → 10 200 ms self  ← bottleneck
```

The bottleneck is the span with the most self time. Ranking by inclusive time would always blame
the root span, which is exactly the mistake a tired human makes at 3 AM.

## Your task

```bash
git grep -n "TODO(step-1)"
```

In `sre_agent/src/sre_agent/gcp_tools.py`, `analyze_trace_cascade` already builds:

* `span_map`: span ID → span
* `children_map`: span ID → list of child span IDs
* `inclusive_durations`: span ID → inclusive ms

Fill in `exclusive_durations` and `bottleneck_span_id`. Use the helper
`_covered_ms(span, children)`: children can run in parallel, and the helper counts overlapping
time once. Clamp at 0.

## Run it

```bash
uv run workshop/check.py 1
uv run simulate_incident.py --engine-only
```

Before: `Bottleneck Span: /api/gateway`, `0 ms`. After:

```text
*   **Bottleneck Span**: `/api/database` (`span-database-333`)
*   **Self-Execution Time**: `10200 ms` (99.3% of total trace)
```

## Why it matters for agents

This is a **tool**: a plain function with type hints and a docstring. The Antigravity and ADK
SDKs turn the signature and docstring into the schema the LLM sees. Deterministic maths belongs in
tools, not prompts: the model decides *when* to measure, and the code makes the measurement right
every time.

**Stuck?** `git diff step-00 step-01 -- sre_agent/` shows the solution, and
`git apply workshop/steps/01-find-the-bottleneck/solution.patch` applies it.
