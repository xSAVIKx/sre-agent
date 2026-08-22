"""HTTP Service Wrapper for the Antigravity SRE Diagnostics Agent.

This module exposes a FastAPI application that wraps the SRE Agent,
allowing it to be deployed to GCP Cloud Run and invoked via HTTP requests.
"""

from sre_common.logging import setup_logging

# Initialize logging before importing anything that creates a logger at
# import time; the E402 noqa below is that ordering, not an oversight.
setup_logging(service_name="sre-agent")

from fastapi import FastAPI  # noqa: E402
from sre_common.middleware import TraceContextMiddleware  # noqa: E402

from sre_agent.routes import router  # noqa: E402

# Initialize FastAPI application
app = FastAPI(
    title="Antigravity Cloud SRE Diagnostics Agent Service",
    description="A cloud-deployable SRE Diagnostics agent service wrapper running on Cloud Run.",
    version="0.1.0",
)

app.add_middleware(TraceContextMiddleware)

# Include SRE agent APIRouter endpoints
app.include_router(router)
