"""Infrastructure Discovery Task executed by Cloud Run Jobs/Tasks."""

import argparse
import json
import logging
import os
import sys
from typing import Any

import httpx

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inventory_scanner")


DATABASE_ASSET_TYPES = {
    "firestore.googleapis.com/Database": "FIRESTORE",
    "spanner.googleapis.com/Instance": "SPANNER",
    "sqladmin.googleapis.com/Instance": "CLOUDSQL",
}


class DiscoveryError(RuntimeError):
    """Raised when the project's services cannot be listed at all."""


def discover_services(project_id: str) -> list[dict[str, Any]]:
    """Lists the project's Cloud Run services in every region via the Cloud Run Admin API.

    Needs `run.services.list` (roles/run.developer or roles/run.viewer).
    """
    from google.cloud import run_v2

    client = run_v2.ServicesClient()
    services = []
    for svc in client.list_services(parent=f"projects/{project_id}/locations/-"):
        services.append(
            {
                "name": svc.name.rsplit("/", 1)[-1],
                "url": svc.uri,
                "region": svc.name.split("/")[3],
                "vpc_connector": svc.template.vpc_access.connector,
            }
        )
    return services


def discover_databases(project_id: str) -> list[dict[str, Any]]:
    """Finds Firestore, Spanner and Cloud SQL databases via Cloud Asset Inventory.

    Needs the Cloud Asset API and `cloudasset.assets.searchAllResources`
    (roles/cloudasset.viewer).
    """
    from google.cloud import asset_v1

    client = asset_v1.AssetServiceClient()
    response = client.search_all_resources(
        request={"scope": f"projects/{project_id}", "asset_types": list(DATABASE_ASSET_TYPES)}
    )
    return [
        {"name": res.display_name or res.name.rsplit("/", 1)[-1], "type": DATABASE_ASSET_TYPES[res.asset_type]}
        for res in response
        if res.asset_type in DATABASE_ASSET_TYPES
    ]


def run_gcp_discovery(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Discovers the target project's Cloud Run services and databases.

    Services are required: if they cannot be listed, DiscoveryError is raised so
    the scan is reported as FAILED instead of caching an empty or invented
    topology. Databases are best-effort (Cloud Asset Inventory may be disabled).
    """
    logger.info(f"Initiating GCP discovery for project: {project_id}")

    try:
        services = discover_services(project_id)
    except Exception as e:
        raise DiscoveryError(f"Could not list Cloud Run services in {project_id}: {e}") from e
    for svc in services:
        logger.info(f"Discovered service: {svc['name']} ({svc['region']})")

    databases: list[dict[str, Any]] = []
    try:
        databases = discover_databases(project_id)
        for db in databases:
            logger.info(f"Discovered database: {db['name']} ({db['type']})")
    except Exception as e:
        logger.warning(f"Cloud Asset Inventory search failed, continuing without databases: {e}")

    aggregated_metadata = {
        "region": os.environ.get("SCANNER_JOB_REGION", "us-central1"),
        "resource_count": len(services) + len(databases),
        "labels": {"scanner": "inventory-scanner-job"},
    }
    return {"services": services, "databases": databases}, aggregated_metadata


def run_mock_discovery(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Loads static mock resource inventory data for local testing."""
    logger.info(f"Loading local offline mock inventory for: {project_id}")

    discovered_resources = {
        "services": [
            {"name": "sre-chaos-monkey", "url": "https://sre-chaos-monkey-mock.run.app", "vpc_connector": "sre-vpc"},
            {"name": "sre-agent", "url": "https://sre-agent-mock.run.app"},
        ],
        "databases": [{"name": "(default)", "type": "FIRESTORE"}],
    }

    aggregated_metadata = {
        "region": "us-central1",
        "resource_count": 3,
        "labels": {"env": "development", "scanner": "inventory-scanner-job-mock"},
    }

    return discovered_resources, aggregated_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Infrastructure Inventory Scanner Job")
    parser.add_argument("--project-id", type=str, help="GCP Project ID to scan")
    args = parser.parse_args()

    # Read config from args or environment variables (GCP Tasks friendly)
    project_id = args.project_id or os.environ.get("TARGET_PROJECT_ID")
    callback_url = os.environ.get("CALLBACK_URL")
    is_mock_env = os.environ.get("MOCK_GCP", "true").lower() in ("true", "1", "yes")

    if not project_id:
        logger.error("Error: project_id is required either via --project-id or TARGET_PROJECT_ID env variable.")
        sys.exit(1)

    logger.info(f"Starting inventory discovery task for project: {project_id} (is_mock={is_mock_env})")

    # Run fingerprinting
    status = "ACTIVE"
    if is_mock_env:
        discovered_resources, aggregated_metadata = run_mock_discovery(project_id)
    else:
        try:
            discovered_resources, aggregated_metadata = run_gcp_discovery(project_id)
        except DiscoveryError as e:
            logger.error(str(e))
            status = "FAILED"
            discovered_resources = {"services": [], "databases": []}
            aggregated_metadata = {"error": str(e)}

    # Post results back to inventory agent callback URL if configured
    if callback_url:
        logger.info(f"Posting discovery callback results to: {callback_url}")
        payload = {
            "project_id": project_id,
            "discovered_resources": discovered_resources,
            "aggregated_metadata": aggregated_metadata,
            "status": status,
        }

        try:
            response = httpx.post(callback_url, json=payload, timeout=30.0)
            if response.status_code == 200:
                logger.info("Callback successfully executed.")
            else:
                logger.error(f"Callback returned error status code: {response.status_code}, response: {response.text}")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to post callback results: {e}")
            sys.exit(1)
    else:
        logger.info("No CALLBACK_URL provided. Printing results to stdout:")
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "discovered_resources": discovered_resources,
                    "aggregated_metadata": aggregated_metadata,
                },
                indent=2,
            )
        )

    if status == "FAILED":
        sys.exit(1)  # already reported; still fail the task so the job execution shows the error


if __name__ == "__main__":
    main()
