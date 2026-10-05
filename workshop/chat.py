"""Starts the web chat on your computer: `uv run workshop/chat.py` (then open http://localhost:8080/chat).

The chat runs in mock mode: it reads the telemetry that `uv run simulate_incident.py` writes,
and it needs no GCP project. This command works the same on macOS, Linux and Windows.

Options:
    --port N  The port for the chat. Default: 8080.
"""

import os
import socket
import sys

import uvicorn


def main(argv: list[str]) -> int:
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8080
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Port {port} is in use. Stop the other program, or use: uv run workshop/chat.py --port 8090")
            return 1
    os.environ.setdefault("MOCK_GCP", "true")
    print(f"The chat is at http://localhost:{port}/chat (press Ctrl+C to stop)")
    uvicorn.run("agent.main:app", port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
