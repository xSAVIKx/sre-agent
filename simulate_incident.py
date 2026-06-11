"""SRE Agent Simulation Launcher.

This script runs the target FastAPI application logic in simulation mode to
generate mock trace and log files, then spins up the Antigravity SRE Agent
locally to analyze the mock files and output a diagnostic report.
"""

import os
import sys
import asyncio
import logging

# Ensure workspace root is in Python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("simulator")

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

    # 1. Generate simulated telemetry
    logger.info("Simulating target application incident (Gateway -> Backend -> Database)...")
    from fastapi import HTTPException
    trace_id = None
    try:
        from app.main import gateway
        # Run gateway request with error=True to trigger database connection error
        # This writes trace details and logs to the local mock directory
        await gateway(trigger_error=True)
    except HTTPException as e:
        if isinstance(e.detail, dict) and "trace_id" in e.detail:
            trace_id = e.detail["trace_id"]
            logger.info(f"Generated simulated incident trace with ID: {trace_id}")
        else:
            logger.warning(f"Gateway threw expected exception: {e.detail}")
    except Exception as e:
        logger.error(f"Failed to generate mock telemetry: {e}")
        return

    # 2. Boot the SRE agent
    logger.info("Booting Antigravity SRE Agent...")
    try:
        from skills.sre_incident_solver.sre_agent import load_agent_config, Agent
        config = load_agent_config()

        logger.info("Invoking agent diagnosis loop...")
        async with Agent(config) as agent:
            # Send prompt to agent
            response = await agent.chat(
                "Gateway service is throwing errors and latency is spiking. Find the root cause."
            )
            report = await response.text()

            # Print the markdown report
            print("\n" + "="*50)
            print("AGENT DIAGNOSIS REPORT")
            print("="*50)
            print(report)
            print("="*50 + "\n")

    except Exception as e:
        logger.exception(f"Failed to run SRE Agent diagnostics: {e}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
