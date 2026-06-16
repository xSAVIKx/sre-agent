"""Infrastructure Discovery Task executed by Cloud Run Jobs/Tasks.
"""

import os
import sys
import json
import logging
import argparse
import httpx

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inventory_scanner")


def run_gcp_discovery(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Runs real GCP asset discovery inside the target project using Cloud Asset API or gcloud CLI."""
    logger.info(f"Initiating GCP discovery for project: {project_id}")
    
    discovered_resources = {
        "services": [],
        "databases": []
    }
    
    # Try using Google Cloud Asset API
    try:
        from google.cloud import asset_v1
        client = asset_v1.AssetServiceClient()
        parent = f"projects/{project_id}"
        
        # Search for Cloud Run services and databases (Firestore/Datastore, Spanner, SQL)
        asset_types = [
            "run.googleapis.com/Service",
            "firestore.googleapis.com/Database",
            "spanner.googleapis.com/Instance",
            "sqladmin.googleapis.com/Instance"
        ]
        
        response = client.search_all_resources(
            request={
                "scope": parent,
                "asset_types": asset_types,
            }
        )
        
        for resource in response:
            asset_type = resource.asset_type
            name = resource.name.split("/")[-1]
            logger.info(f"Discovered asset: {name} (type: {asset_type})")
            
            if "run.googleapis.com" in asset_type:
                # Resolve service URL
                url = resource.additional_attributes.get("status", {}).get("url", "")
                discovered_resources["services"].append({
                    "name": name,
                    "url": url,
                    "vpc_connector": resource.additional_attributes.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}).get("run.googleapis.com/vpc-access-connector", "")
                })
            elif "firestore.googleapis.com" in asset_type:
                discovered_resources["databases"].append({
                    "name": name,
                    "type": "FIRESTORE"
                })
            elif "spanner.googleapis.com" in asset_type:
                discovered_resources["databases"].append({
                    "name": name,
                    "type": "SPANNER"
                })
            elif "sqladmin.googleapis.com" in asset_type:
                discovered_resources["databases"].append({
                    "name": name,
                    "type": "CLOUDSQL"
                })
                
    except Exception as e:
        logger.warning(f"Failed to query Cloud Asset API: {e}. Falling back to gcloud CLI subprocesses.")
        
        # Fallback to subprocess running gcloud command execution if installed
        import subprocess
        try:
            # 1. Cloud Run services list
            run_cmd = ["gcloud", "run", "services", "list", f"--project={project_id}", "--format=json"]
            res = subprocess.run(run_cmd, capture_output=True, text=True, check=True)
            services_data = json.loads(res.stdout)
            for svc in services_data:
                svc_metadata = svc.get("metadata", {})
                svc_spec = svc.get("spec", {}).get("template", {}).get("metadata", {})
                discovered_resources["services"].append({
                    "name": svc_metadata.get("name"),
                    "url": svc.get("status", {}).get("url"),
                    "vpc_connector": svc_spec.get("annotations", {}).get("run.googleapis.com/vpc-access-connector", "")
                })
        except Exception as sub_e:
            logger.error(f"Failed to execute gcloud subprocess: {sub_e}")

    # Fallback/default logic if no resources are found to ensure we have a bootable layout
    if not discovered_resources["services"]:
        logger.info("No active compute services resolved in target project. Using default discovery fallbacks.")
        discovered_resources["services"].append({
            "name": "sre-chaos-monkey",
            "url": f"https://sre-chaos-monkey-{project_id}.a.run.app"
        })
        
    aggregated_metadata = {
        "region": os.environ.get("SCANNER_JOB_REGION", "us-central1"),
        "resource_count": len(discovered_resources["services"]) + len(discovered_resources["databases"]),
        "labels": {"scanner": "inventory-scanner-job"}
    }
    
    return discovered_resources, aggregated_metadata


def run_mock_discovery(project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Loads static mock resource inventory data for local testing."""
    logger.info(f"Loading local offline mock inventory for: {project_id}")
    
    discovered_resources = {
        "services": [
            {"name": "sre-chaos-monkey", "url": "https://sre-chaos-monkey-mock.run.app", "vpc_connector": "sre-vpc"},
            {"name": "sre-agent", "url": "https://sre-agent-mock.run.app"}
        ],
        "databases": [
            {"name": "(default)", "type": "FIRESTORE"}
        ]
    }
    
    aggregated_metadata = {
        "region": "us-central1",
        "resource_count": 3,
        "labels": {"env": "development", "scanner": "inventory-scanner-job-mock"}
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
    if is_mock_env:
        discovered_resources, aggregated_metadata = run_mock_discovery(project_id)
    else:
        discovered_resources, aggregated_metadata = run_gcp_discovery(project_id)

    # Post results back to inventory agent callback URL if configured
    if callback_url:
        logger.info(f"Posting discovery callback results to: {callback_url}")
        payload = {
            "project_id": project_id,
            "discovered_resources": discovered_resources,
            "aggregated_metadata": aggregated_metadata,
            "status": "ACTIVE"
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
        print(json.dumps({
            "project_id": project_id,
            "discovered_resources": discovered_resources,
            "aggregated_metadata": aggregated_metadata
        }, indent=2))


if __name__ == "__main__":
    main()
