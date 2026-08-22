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

from inventory_agent.routes import router  # noqa: E402

# Initialize FastAPI application
app = FastAPI(
    title="GCP Infrastructure Topology Inventory Agent Service",
    description="A cloud-deployable Inventory agent service wrapper running on Cloud Run.",
    version="0.1.0",
)

app.add_middleware(TraceContextMiddleware)

# Include SRE agent APIRouter endpoints
app.include_router(router)
