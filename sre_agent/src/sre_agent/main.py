"""HTTP Service Wrapper for the Antigravity SRE Diagnostics Agent.

This module exposes a FastAPI application that wraps the SRE Agent,
allowing it to be deployed to GCP Cloud Run and invoked via HTTP requests.
"""

from sre_common.logging import setup_logging

# Initialize logging before importing anything that creates a logger at
# import time; the E402 noqa below is that ordering, not an oversight.
setup_logging(service_name="sre-agent")

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from sre_common.middleware import TraceContextMiddleware  # noqa: E402

from sre_agent.a2a_agent import build_a2a_app  # noqa: E402
from sre_agent.config import A2A_PUBLIC_URL  # noqa: E402
from sre_agent.routes import router  # noqa: E402

# The SRE engine as an A2A agent: JSON-RPC at "/", card at /.well-known/agent-card.json.
a2a_app = build_a2a_app(A2A_PUBLIC_URL)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A mounted Starlette app's lifespan never runs on its own, and to_a2a() registers
    # its routes there - so run it as part of ours.
    async with a2a_app.router.lifespan_context(a2a_app):
        yield


# Initialize FastAPI application
app = FastAPI(
    title="Antigravity Cloud SRE Diagnostics Agent Service",
    description="The SRE diagnostics engine, served over A2A, plus a small REST API for traces.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(TraceContextMiddleware)

# REST endpoints (/health, /trace) first, so the root mount below doesn't shadow them.
app.include_router(router)
app.mount("/", a2a_app)
