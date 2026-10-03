# Step 2 · Feed the metrics (12 min)

**Goal:** the report's **Observability Metrics** section shows real numbers instead of
`No CPU utilization data available.`

## The idea: tools need data, mocks need the right shape

Every tool in this repo has an `if IS_MOCK:` branch that reads local JSON instead of calling Google
Cloud. That's what lets the whole workshop run offline. `query_metrics` reads
`mock_telemetry_data/metrics.json`, but nothing writes that file yet.

The SRE engine asks two questions (see `sre_workflow.py`):

```
metric.type="run.googleapis.com/container/cpu/utilizations"              AND service_name="sre-chaos-monkey"
metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" AND database_id="db-primary"
```

## Your task

```bash
git grep -n "TODO(step-2)"
```

In `app/main.py`, make `_mock_metric_series(trigger_error)` return two Cloud Monitoring–style
time series:

```python
{
    "metric": {"type": CPU_METRIC, "labels": {"service_name": "sre-chaos-monkey"}},
    "points": [{"value": 0.18}, {"value": 0.21}, {"value": 0.24}],
}
```

* CPU readings are fractions between 0 and 1.
* During the incident (`trigger_error=True`) the **last** DB connection reading must be
  `DB_MAX_CONNECTIONS`: the pool is exhausted, which is the story the metrics should tell.

## Run it

```bash
uv run workshop/check.py 2
uv run simulate_incident.py --engine-only
```

After:

```text
- **CPU Utilization (sre-chaos-monkey)**: `24.0% (Healthy)`
- **Database Connections (db-primary)**: `100 connections (Warning: Max capacity reached)`
```

Low CPU plus a full connection pool tells you the app isn't busy, it's *waiting*. That's evidence
that corroborates the trace.

**Stuck?** `git apply workshop/steps/02-feed-the-metrics/solution.patch`
