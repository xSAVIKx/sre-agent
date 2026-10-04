"""The optional, model-written part of a post-mortem.

The post-mortem itself is rendered from the trace's evidence (`gcp_tools.generate_post_mortem`),
so it is the same with or without a model. When a Gemini key is configured, an ADK agent
reads that evidence and adds an "Analyst Notes" section: likely causes, what to check
next, how to prevent a repeat. The section is marked as model-written, and any failure
leaves the post-mortem without it rather than failing the request.
"""

import logging
import os
import uuid

from sre_common import otel_trace

logger = logging.getLogger("sre_agent.post_mortem_analysis")

ANALYSIS_MODEL = os.getenv("SRE_ANALYSIS_MODEL", "gemini-3.8-flash")
ANALYSIS_HEADING = "## 🤖 Analyst Notes (AI-generated)"

ANALYST_INSTRUCTION = (
    "You are a senior SRE reviewing an incident post-mortem draft. The draft was generated "
    "from the incident's trace spans and logs, and is the only evidence you have. Write "
    "concise Markdown with three short bullet lists under the bold labels **Likely causes**, "
    "**Check next** and **Prevent a repeat**. Ground every point in the draft's evidence and "
    "say so when the evidence is thin. Do not repeat the draft, do not invent services, "
    "metrics or errors it does not mention, and do not add a top-level heading."
)


def analysis_enabled() -> bool:
    """True when a Gemini key is configured (and ADK is importable)."""
    if not os.environ.get("GEMINI_API_KEY"):
        return False
    try:
        import google.adk  # noqa: F401
    except ImportError:
        return False
    return True


@otel_trace("analyze_post_mortem")
async def analyze_post_mortem(post_mortem: str, question: str = "") -> str:
    """The model-written "Analyst Notes" section for a post-mortem, or "" without a model.

    Args:
        post_mortem: The evidence-based post-mortem Markdown.
        question: The user's request, if any, so the notes can address it.
    """
    if not analysis_enabled():
        return ""
    try:
        from google.adk import Agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        analyst = Agent(name="post_mortem_analyst", model=ANALYSIS_MODEL, instruction=ANALYST_INSTRUCTION)
        sessions = InMemorySessionService()
        runner = Runner(agent=analyst, app_name="post_mortem_analysis", session_service=sessions)
        session = await sessions.create_session(
            app_name="post_mortem_analysis", user_id="sre", session_id=uuid.uuid4().hex
        )

        prompt = f"Post-mortem draft:\n\n{post_mortem}"
        if question:
            prompt = f"The user asked: {question}\n\n{prompt}"
        message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

        notes = ""
        async for event in runner.run_async(user_id="sre", session_id=session.id, new_message=message):
            if event.author == analyst.name and event.content and event.content.parts:
                notes += "".join(p.text or "" for p in event.content.parts if not p.thought)
        notes = notes.strip()
        return f"{ANALYSIS_HEADING}\n\n{notes}" if notes else ""
    except Exception as e:
        logger.warning(f"Post-mortem analysis unavailable, returning the post-mortem without it: {e}")
        return ""
