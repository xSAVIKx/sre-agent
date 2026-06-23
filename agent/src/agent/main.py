"""HTTP Service Wrapper for the Antigravity SRE Agent.

This module exposes a FastAPI application that wraps the Antigravity agent,
allowing it to be deployed to GCP Cloud Run and invoked via HTTP requests.
"""

from sre_common.logging import setup_logging
from sre_common.middleware import TraceContextMiddleware
# Initialize logging configuration before importing other modules
setup_logging(service_name="orchestrator-agent")


from fastapi import FastAPI
from agent.routes import router

# Initialize FastAPI application
app = FastAPI(
    title="Antigravity Cloud SRE Agent Service",
    description="A cloud-deployable SRE agent service wrapper running on Cloud Run.",
    version="0.1.0"
)

app.add_middleware(TraceContextMiddleware)

# Include SRE agent APIRouter endpoints
app.include_router(router)
