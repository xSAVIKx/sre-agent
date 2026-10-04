"""SRE Agent Simulation Launcher.

This script runs the target FastAPI application logic in simulation mode to
generate mock trace and log files, then spins up the Antigravity SRE Agent
locally to analyze the mock files and output a diagnostic report.

Flags:
    --engine-only  Run the SRE diagnostics engine directly, without the Orchestrator.
    --keep-data    Keep telemetry from previous runs instead of starting clean.
"""

import asyncio
import logging
import os
import shutil
import sys

# Ensure workspace root is in Python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("simulator")


def _print_report(title: str, report: str) -> None:
    print("\n" + "=" * 50)
    print(title)
    print("=" * 50)
    print(report)
    print("=" * 50 + "\n")


async def run_simulation() -> None:
    """Runs the SRE diagnostics simulation.

    1. Triggers the mock target app gateway with error=True to populate mock telemetry.
    2. Instantiates the Antigravity agent.
    3. Runs the agentic diagnostics query.
    """
    logger.info("Initializing SRE incident simulator...")

    # Force mock mode for the local run
    os.environ["MOCK_GCP"] = "true"
    os.environ["MOCK_DATA_DIR"] = "mock_telemetry_data"
    os.environ["GCP_PROJECT"] = "simulation-project-123"
    # Run the diagnostics workflow in-process rather than calling a sub-agent service.
    os.environ.pop("SRE_AGENT_URL", None)

    # Start every run from a clean slate so the report always describes this run's incident.
    if "--keep-data" not in sys.argv and os.path.isdir(os.environ["MOCK_DATA_DIR"]):
        shutil.rmtree(os.environ["MOCK_DATA_DIR"])
        logger.info("Cleared previous mock telemetry (pass --keep-data to accumulate runs).")

    # 1. Generate simulated telemetry
    logger.info("Simulating target application incident (Gateway -> Backend -> Database)...")
    from fastapi import HTTPException

    trace_id = None
    try:
        from fastapi import Request

        from app.main import gateway

        # Create a dummy Request for the FastAPI endpoint
        scope = {"type": "http", "headers": []}
        request = Request(scope)
        # Run gateway request with error=True to trigger database connection error
        # This writes trace details and logs to the local mock directory
        await gateway(request, trigger_error=True)
    except HTTPException as e:
        if isinstance(e.detail, dict) and "trace_id" in e.detail:
            trace_id = e.detail["trace_id"]
            logger.info(f"Generated simulated incident trace with ID: {trace_id}")
        else:
            logger.warning(f"Gateway threw expected exception: {e.detail}")
    except Exception as e:
        logger.error(f"Failed to generate mock telemetry: {e}")
        return

    # 2a. --engine-only: run the SRE diagnostics engine directly, skipping the
    # Orchestrator agent and its safety policy (handy before the policy allows it).
    if "--engine-only" in sys.argv:
        logger.info("Running the SRE diagnostics engine directly (--engine-only)...")
        from sre_agent.gcp_tools import query_traces
        from sre_agent.sre_workflow import run_sre_diagnostics

        report = await run_sre_diagnostics(await query_traces(limit=10), project_id=os.environ["GCP_PROJECT"])
        _print_report("SRE ENGINE REPORT (no Orchestrator)", report)
        return

    # 2. Boot the SRE agent
    logger.info("Booting Antigravity SRE Agent...")
    try:
        from agent.config import Agent, DiagnosisSink, diagnosis_sink, load_agent_config

        config = load_agent_config()
        # The Orchestrator replies with a short summary; the tool's full report lands here
        # (the chat UI shows it as a card under the reply).
        sink = DiagnosisSink()
        diagnosis_sink.set(sink)

        logger.info("Invoking agent diagnosis loop...")
        async with Agent(config) as agent:
            # Send prompt to agent
            response = await agent.chat(
                "Gateway service is throwing errors and latency is spiking. Find the root cause."
            )
            reply = await response.text()

            if sink.report:
                _print_report("AGENT REPLY", reply)
            _print_report("AGENT DIAGNOSIS REPORT", sink.report or reply)

    except Exception as e:
        logger.exception(f"Failed to run SRE Agent diagnostics: {e}")


if __name__ == "__main__":
    asyncio.run(run_simulation())
