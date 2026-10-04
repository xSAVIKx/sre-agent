#!/usr/bin/env bash
#
# cleanup.sh - Graceful tear-down script to delete the demo deployment resources from GCP
#
# Removes everything deploy.sh creates: the four Cloud Run services, the inventory
# scanner job, the Artifact Registry repository, the GEMINI_API_KEY secret, and the
# four service accounts with their project-level role bindings. Optionally deletes the
# Firestore (default) database and the local .env / mock telemetry.
#
# Pass --yes (or -y) to answer "yes" to the optional prompts (non-interactive runs).

set -euo pipefail

ASSUME_YES=false
for arg in "$@"; do
    case $arg in
        --yes|-y) ASSUME_YES=true ;;
    esac
done

# Add default Windows Google Cloud SDK path to PATH if present (Git Bash or WSL)
if [ -d "/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin" ]; then
    export PATH="/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin:$PATH"
elif [ -d "/mnt/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin" ]; then
    export PATH="/mnt/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin:$PATH"
fi

# ANSI color codes for logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

confirm() {
    # confirm "<question>" -> returns 0 for yes
    if [ "$ASSUME_YES" = "true" ]; then
        return 0
    fi
    local answer
    read -r -p "$1 (y/n): " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}    GCP SRE Agent Codelab Resource Cleanup     ${NC}"
echo -e "${BLUE}===============================================${NC}"

# 1. Load configuration from .env if available
if [ -f .env ]; then
    echo "Loading configuration from .env..."
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
else
    echo -e "${YELLOW}Warning: .env file not found.${NC}"
    read -r -p "Enter GCP Project ID: " GCP_PROJECT
    read -r -p "Enter GCP Region [default: us-central1]: " GCP_REGION
    GCP_REGION=${GCP_REGION:-us-central1}
fi

if [ -z "${GCP_PROJECT:-}" ] || [ -z "${GCP_REGION:-}" ]; then
    echo -e "${RED}Error: GCP_PROJECT and GCP_REGION must be specified.${NC}"
    exit 1
fi

echo "Targeting resources in Project: $GCP_PROJECT, Region: $GCP_REGION"
echo ""

# Set project context
gcloud config set project "$GCP_PROJECT"

# 2. Delete Cloud Run services and the scanner job
echo -e "${BLUE}[1/4] Deleting Cloud Run services and jobs...${NC}"

for SERVICE in sre-agent sre-sub-agent inventory-agent sre-chaos-monkey; do
    if gcloud run services describe "$SERVICE" --region "$GCP_REGION" &>/dev/null; then
        gcloud run services delete "$SERVICE" --region "$GCP_REGION" --quiet
        echo -e "${GREEN}✓ Deleted Cloud Run service: $SERVICE${NC}"
    else
        echo "• Service '$SERVICE' does not exist."
    fi
done

JOB_NAME="inventory-scanner-job"
if gcloud run jobs describe "$JOB_NAME" --region "$GCP_REGION" &>/dev/null; then
    gcloud run jobs delete "$JOB_NAME" --region "$GCP_REGION" --quiet
    echo -e "${GREEN}✓ Deleted Cloud Run job: $JOB_NAME${NC}"
else
    echo "• Job '$JOB_NAME' does not exist."
fi

# 3. Delete the image repository and the API key secret
echo -e "\n${BLUE}[2/4] Deleting Artifact Registry repository and secrets...${NC}"

REPO_NAME="sre-repo"
if gcloud artifacts repositories describe "$REPO_NAME" --location="$GCP_REGION" &>/dev/null; then
    gcloud artifacts repositories delete "$REPO_NAME" --location="$GCP_REGION" --quiet
    echo -e "${GREEN}✓ Deleted Artifact Registry repository: $REPO_NAME${NC}"
else
    echo "• Artifact Registry repository '$REPO_NAME' does not exist."
fi

# Deleting the secret also removes its secretAccessor bindings.
if gcloud secrets describe GEMINI_API_KEY &>/dev/null; then
    gcloud secrets delete GEMINI_API_KEY --quiet
    echo -e "${GREEN}✓ Deleted secret: GEMINI_API_KEY${NC}"
else
    echo "• Secret 'GEMINI_API_KEY' does not exist."
fi

# 4. Remove IAM role bindings & service accounts
echo -e "\n${BLUE}[3/4] Cleaning up IAM policies and Service Accounts...${NC}"

# delete_sa <name> <role>... : drops the project-level bindings deploy.sh granted, then the SA.
# Bindings are removed first because deleting an SA leaves "deleted:serviceAccount:..."
# members behind in the project policy.
delete_sa() {
    local sa_name="$1"
    shift
    local sa_email="${sa_name}@${GCP_PROJECT}.iam.gserviceaccount.com"
    if ! gcloud iam service-accounts describe "$sa_email" &>/dev/null; then
        echo "• Service account '$sa_email' does not exist."
        return
    fi
    echo "Removing IAM policy bindings for $sa_name..."
    local role
    for role in "$@"; do
        gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
            --member="serviceAccount:${sa_email}" \
            --role="$role" &>/dev/null || true
    done
    gcloud iam service-accounts delete "$sa_email" --quiet
    echo -e "${GREEN}✓ Deleted service account: $sa_email${NC}"
}

delete_sa sre-chaos-monkey-sa roles/cloudtrace.agent roles/logging.logWriter
delete_sa sre-agent-sa roles/cloudtrace.user roles/cloudtrace.agent roles/logging.viewer roles/monitoring.viewer roles/datastore.user
delete_sa inventory-agent-sa roles/datastore.user roles/run.developer roles/logging.logWriter roles/cloudasset.viewer roles/cloudtrace.agent
delete_sa sre-build-sa roles/logging.logWriter roles/storage.admin roles/run.admin roles/artifactregistry.writer

# 5. Optional: Firestore and local files
echo -e "\n${BLUE}[4/4] Optional cleanup...${NC}"

if gcloud firestore databases describe --database="(default)" &>/dev/null; then
    if confirm "Delete the Firestore (default) database (sessions, inventory cache, templates)?"; then
        gcloud firestore databases delete --database="(default)" --quiet
        echo -e "${GREEN}✓ Deleted Firestore (default) database.${NC}"
    else
        echo "• Kept the Firestore (default) database."
    fi
fi

if confirm "Delete the local .env and mock telemetry directories?"; then
    rm -f .env
    rm -rf mock_telemetry_data/
    echo -e "${GREEN}✓ Deleted local .env file and mock_telemetry_data/ folder.${NC}"
else
    echo "• Kept local configuration and mock telemetry data."
fi

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}      Demo Stack Resources Torn Down!          ${NC}"
echo -e "${GREEN}===============================================${NC}"
echo "Enabled APIs and Cloud Build source buckets are left in place."
echo "To remove everything at once, delete the project: gcloud projects delete $GCP_PROJECT"
