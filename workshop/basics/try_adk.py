"""Runs one ADK agent alone and shows each event: `uv run workshop/basics/try_adk.py`.

The agent is the LogCorrelator of the SRE agent (sre_agent/src/sre_agent/sre_workflow.py).
It gets the spans and logs of one failed request. Look at the events: the model asks for
a tool, ADK runs the tool and gives the result back, and the model writes its answer.

Without GEMINI_API_KEY, the model is the scripted SimulatedLlm. With a key, it is Gemini.
Run `uv run simulate_incident.py --engine-only` first, so the metrics tool finds data.
"""

import asyncio
import json
import os
import sys

os.environ.setdefault("MOCK_GCP", "true")
sys.stdout.reconfigure(encoding="utf-8")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from sre_agent.sre_workflow import log_correlator  # noqa: E402

SPANS = {"spans": [{"name": "/api/database", "status": "ERROR", "durationMs": 10200}]}
LOGS = [{"severity": "CRITICAL", "message": "ConnectionTimeoutError: db-primary:5432 after 10000ms"}]


async def main() -> None:
    model = getattr(log_correlator.model, "model", log_correlator.model)
    tools = [getattr(t, "__name__", str(t)) for t in log_correlator.tools]
    print(f"Agent: {log_correlator.name} · model: {model} · tools: {tools or 'none'}\n")

    sessions = InMemorySessionService()
    runner = Runner(agent=log_correlator, app_name="try_adk", session_service=sessions)
    session = await sessions.create_session(app_name="try_adk", user_id="you")
    prompt = f"Trace Spans:\n{json.dumps(SPANS)}\n\nCorrelated Logs:\n{json.dumps(LOGS)}\n\nFind the root cause."
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    async for event in runner.run_async(user_id="you", session_id=session.id, new_message=message):
        for call in event.get_function_calls():
            print(f"🧠 The model asks for a tool: {call.name}({json.dumps(call.args)})")
        for response in event.get_function_responses():
            result = str(response.response.get("result", response.response))
            print(f"🔧 ADK ran {response.name}, and gives the result to the model: {result[:90]!r}…")
        text = "".join(p.text or "" for p in (event.content.parts if event.content else []))
        if text:
            print(f"💬 The model answers:\n{text}")

    state = (await sessions.get_session(app_name="try_adk", user_id="you", session_id=session.id)).state
    print(f"\n📦 Session state after the run: {state}")


if __name__ == "__main__":
    asyncio.run(main())
