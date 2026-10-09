"""Talks A2A to the SRE agent with plain HTTP and JSON: `uv run workshop/basics/try_a2a.py`.

It starts the SRE agent on a local port, reads its agent card, and sends one JSON-RPC
request. Then it shows each streamed event: the task, its status updates and the result.
No A2A library is used on this side, so you see the protocol itself.

Options:
    SKILL      The skill to call (default: list_incidents). Try diagnose_incident.
    --ui       Ask for A2UI too: the result then has application/json+a2ui data parts. At the
               end, the script shows them in full: paste them into the playground.
    --raw      Show the full JSON of each event.

Run `uv run simulate_incident.py --engine-only` first, so there is an incident.
"""

import asyncio
import json
import logging
import os
import sys
import uuid

os.environ.setdefault("MOCK_GCP", "true")
sys.stdout.reconfigure(encoding="utf-8")
# Only the protocol in the output. A missing incident gets its own hint (see main).
logging.basicConfig(level=logging.ERROR)

import httpx  # noqa: E402
from agent.local_sre import local_sre_agent_url  # noqa: E402

A2UI_EXTENSION = "https://a2ui.org/a2a-extension/a2ui/v0.9"
A2UI_MIME_TYPE = "application/json+a2ui"
SRE_CATALOG = "https://github.com/xSAVIKx/sre-agent/a2ui/catalogs/sre/v1/catalog.json"


def _a2ui(message: dict) -> str:
    """One A2UI message in one line: its type and what it sends."""
    kind, body = next((k, v) for k, v in message.items() if k != "version")
    if kind == "updateComponents":
        types = [c.get("component", "?") for c in body.get("components", [])]
        return f"{kind}: {len(types)} components ({', '.join(dict.fromkeys(types))})"
    if kind == "updateDataModel":
        return f"{kind}: {json.dumps(body.get('value'), ensure_ascii=False)[:50]}…"
    return f"{kind}: {body.get('surfaceId', '')}"


def _parts(parts: list[dict]) -> str:
    """One line for each part: its type and the start of its content."""
    lines = []
    for part in parts:
        mime = (part.get("metadata") or {}).get("mimeType", "")
        if "text" in part:
            lines.append(f"text: {part['text'][:70]!r}")
        elif "data" in part and mime == A2UI_MIME_TYPE:
            lines.append(f"data ({mime}): {_a2ui(part['data'])}")
        elif "data" in part:
            lines.append(
                f"data{' (' + mime + ')' if mime else ''}: {json.dumps(part['data'], ensure_ascii=False)[:60]}…"
            )
    return "\n        ".join(lines)


async def main(argv: list[str]) -> None:
    skill = next((a for a in argv if not a.startswith("--")), "list_incidents")
    if not os.path.exists(os.path.join(os.environ.get("MOCK_DATA_DIR", "mock_telemetry_data"), "traces.json")):
        print("⚠️  There is no incident yet, so the agent finds nothing. First run:")
        print("   uv run simulate_incident.py --engine-only\n")
    url = await asyncio.to_thread(local_sre_agent_url)
    async with httpx.AsyncClient(base_url=url, timeout=120) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
        print(f"📇 GET {url}/.well-known/agent-card.json")
        print(f"   {card['name']}: {card['description']}")
        for s in card["skills"]:
            print(f"   - skill {s['id']}: {s['name']}")
        print(f"   extensions: {[e['uri'] for e in card['capabilities'].get('extensions', [])]}\n")

        message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": "What is failing?"}]}
        metadata = {"skill": skill}
        if "--ui" in argv:
            message["extensions"] = [A2UI_EXTENSION]
            metadata["a2uiClientCapabilities"] = {"v0.9": {"supportedCatalogIds": [SRE_CATALOG]}}
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendStreamingMessage",
            "params": {"message": message, "metadata": metadata},
        }
        # A2A-Version selects the protocol version (1.0: method names like SendStreamingMessage).
        headers = {"A2A-Version": "1.0", "Accept": "text/event-stream"}
        print(f"📨 POST {url}/  (JSON-RPC, headers {headers})\n{json.dumps(request, indent=2)}\n")

        surface: list[dict] = []
        async with http.stream("POST", "/", json=request, headers=headers) as response:
            print(f"📡 The answer is a stream ({response.headers['content-type']}):")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                result = json.loads(line[5:])["result"]
                artifact_parts = (result.get("artifactUpdate", {}).get("artifact") or {}).get("parts") or []
                surface += [
                    p["data"] for p in artifact_parts if (p.get("metadata") or {}).get("mimeType") == A2UI_MIME_TYPE
                ]
                if "--raw" in argv:
                    print(json.dumps(result, indent=2))
                    continue
                kind, body = next(iter(result.items()))
                state = (body.get("status") or {}).get("state", "the result (an artifact)")
                parts = ((body.get("status") or {}).get("message") or {}).get("parts") or []
                parts += (body.get("artifact") or {}).get("parts") or []
                print(f"  {kind:<15} {state}")
                if parts:
                    print(f"        {_parts(parts)}")

        if surface and "--raw" not in argv:
            print("\n🎨 The A2UI messages of the result, in full. To see the UI, paste them into the playground")
            print("   (uv run workshop/chat.py, then http://localhost:8080/playground):")
            print(json.dumps(surface, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
