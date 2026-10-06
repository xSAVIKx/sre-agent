"""Starts the SRE agent next to the Orchestrator, so a laptop run uses real A2A too.

On Cloud Run and in docker-compose, SRE_AGENT_URL points at the SRE agent service.
On a laptop (MOCK_GCP=true and no SRE_AGENT_URL), the Orchestrator starts the SRE
agent's A2A app itself: on a free local port, in a background thread. Then each
call goes the same way as in production: the agent card, a JSON-RPC message,
streamed progress, and the result as text, data and A2UI parts.
"""

import logging
import socket
import threading
import time

import uvicorn

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_url: str | None = None


def local_sre_agent_url(timeout: float = 30.0) -> str:
    """The URL of the local SRE agent. The first call starts it, and blocks until it answers."""
    global _url
    with _lock:
        if _url is None:
            from sre_agent.a2a_agent import build_a2a_app

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            server = uvicorn.Server(
                uvicorn.Config(build_a2a_app(url), host="127.0.0.1", port=port, log_level="warning")
            )
            thread = threading.Thread(target=server.run, name="local-sre-agent", daemon=True)
            thread.start()
            deadline = time.monotonic() + timeout
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError(f"The local SRE agent did not start on {url}")
                time.sleep(0.05)
            logger.info(f"Started the SRE agent locally for A2A calls: {url}")
            _url = url
        return _url
