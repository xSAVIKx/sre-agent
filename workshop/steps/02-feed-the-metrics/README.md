# Step 2 · Feed the metrics (12 min)

**Goal:** Make the **Observability Metrics** section of the report show numbers, not
`No CPU utilization data available.`

## The idea: mock data must have the correct shape

Each tool in this repository has an `if IS_MOCK:` branch. This branch reads local JSON and does
not call Google Cloud. Thus, the workshop runs offline.

`query_metrics` reads `mock_telemetry_data/metrics.json`. The target app writes this file, but
`_mock_metric_series` returns an empty list. Thus, the file contains no time series.

The SRE agent uses these two filters (see `sre_workflow.py`):

```
metric.type="run.googleapis.com/container/cpu/utilizations"              AND resource.labels.service_name="sre-chaos-monkey"
metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" AND resource.labels.database_id="db-primary"
```

## Your task

1. Find the TODO:

   ```bash
   git grep -n "TODO(step-2)"
   ```

2. Open `app/main.py` and find `_mock_metric_series(trigger_error)`.
3. Make the function return two time series in the Cloud Monitoring format.
4. Use `CPU_METRIC` with the label `service_name="sre-chaos-monkey"` for the first time series.
5. Use `DB_CONNECTIONS_METRIC` with the label `database_id="db-primary"` for the second time series.

Each time series has this shape:

```python
{
    "metric": {"type": CPU_METRIC, "labels": {"service_name": "sre-chaos-monkey"}},
    "points": [{"value": 0.18}, {"value": 0.21}, {"value": 0.24}],
}
```

Rules for the values:

* A CPU value is a fraction from 0 to 1.
* During the incident (`trigger_error=True`), the **last** DB connection value must be
  `DB_MAX_CONNECTIONS`. This value shows that the connection pool is full.

## Run it

```bash
uv run workshop/check.py 2
uv run simulate_incident.py --engine-only
```

After your change, the report shows:

```text
- **CPU Utilization (sre-chaos-monkey)**: `24.0% (Healthy)`
- **Database Connections (db-primary)**: `100 connections (Warning: Max capacity reached)`
```

## Discuss

The CPU usage is low and the connection pool is full. Thus, the app is not busy: it *waits*. This
data supports the result of the trace analysis from step 1.

**Stuck?** `git apply workshop/steps/02-feed-the-metrics/solution.patch`
