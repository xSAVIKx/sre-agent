"""HTTP Service Wrapper for the GCP Infrastructure Topology Inventory Agent.

This module exposes a FastAPI application that wraps the Inventory agent,
allowing it to be deployed to GCP Cloud Run and invoked via HTTP requests.
"""

from sre_common.logging import setup_logging

# Initialize logging before importing anything that creates a logger at
# import time; the E402 noqa below is that ordering, not an oversight.
setup_logging(service_name="inventory-agent")

from fastapi import FastAPI  # noqa: E402
from sre_common.middleware import TraceContextMiddleware  # noqa: E402
from sre_common.tracing import setup_tracing  # noqa: E402

from inventory_agent.a2a_server import build_a2a_routes  # noqa: E402
from inventory_agent.config import A2A_PUBLIC_URL  # noqa: E402
from inventory_agent.routes import router  # noqa: E402

# Initialize FastAPI application
app = FastAPI(
    title="GCP Infrastructure Topology Inventory Agent Service",
    description="A cloud-deployable Inventory agent service wrapper running on Cloud Run.",
    version="0.1.0",
)

app.add_middleware(TraceContextMiddleware)

# REST: /health, plus /v1/agents/inventory/{refresh,callback} for the scanner job.
app.include_router(router)

# The agent itself, over A2A: JSON-RPC at "/", card at /.well-known/agent-card.json.
app.router.routes.extend(build_a2a_routes(A2A_PUBLIC_URL))

# Spans for each request, exported to Cloud Trace.
setup_tracing(app, service_name="inventory-agent")
