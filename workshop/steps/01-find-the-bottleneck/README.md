# Step 1 · Find the bottleneck (12 min)

**Goal:** Make `analyze_trace_cascade` show the span that is *really* slow.

## The idea: inclusive time and exclusive time

A trace is a tree of spans.

The **inclusive** time of a span is its wall-clock duration. It *includes* the time of its
children. The gateway span lasts 10 270 ms. For almost all of this time, the gateway waits for the
backend. The backend waits for the database.

The **exclusive** (self) time of a span is the time that its children do not cover:

```
exclusive = inclusive − time covered by its children
```

```
/api/gateway   ██████████████████████████████  10 270 ms inclusive →    20 ms self
 /api/backend   █████████████████████████████  10 250 ms inclusive →    50 ms self
  /api/database  ████████████████████████████  10 200 ms inclusive → 10 200 ms self  ← bottleneck
```

The bottleneck is the span with the largest self time. If you sort by inclusive time, the root
span is always first. This is a frequent human error during an incident.

## Your task

1. Find the TODO:

   ```bash
   git grep -n "TODO(step-1)"
   ```

2. Open `sre_agent/src/sre_agent/gcp_tools.py` and find `_cascade(spans)`.
3. Calculate `exclusive_durations`: for each span, the inclusive time minus the time that its
   children cover.
4. Use the helper `_covered_ms(span, children)` to get the covered time.
5. Make sure that no exclusive time is less than 0.
6. Set `bottleneck_span_id` to the span with the largest exclusive time.

`_cascade(spans)` does the calculation for the `analyze_trace_cascade` tool and the post-mortem.
It already makes these dictionaries:

| Name | Key | Value |
|:--|:--|:--|
| `span_map` | span ID | span |
| `children_map` | span ID | list of child span IDs |
| `inclusive_durations` | span ID | inclusive time in ms |

Children can run in parallel. `_covered_ms` counts the time where children overlap only one time.

## Run it

```bash
uv run workshop/check.py 1
uv run simulate_incident.py --engine-only
```

Before your change, the report shows `Bottleneck Span: /api/gateway` and `0 ms`. After your
change, the report shows:

```text
*   **Bottleneck Span**: `/api/database` (`span-database-333`)
*   **Self-Execution Time**: `10200 ms` (99.3% of total trace)
```

## Discuss

`analyze_trace_cascade` is a **tool**: a Python function with type hints and a docstring. The
Antigravity SDK and the ADK SDK make the LLM tool schema from the signature and the docstring.

Put deterministic calculations in tools, not in prompts. The model decides *when* to do the
measurement. The code makes sure that the measurement is always correct.

**Stuck?**

* To show the solution, run `git diff step-00 step-01 -- sre_agent/`.
* To apply the solution, run `git apply workshop/steps/01-find-the-bottleneck/solution.patch`.
