#!/usr/bin/env bash
#
# cleanup.sh - Graceful tear-down script to delete the demo deployment resources from GCP
#

set -euo pipefail

# ANSI color codes for logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}    GCP SRE Agent Codelab Resource Cleanup     ${NC}"
echo -e "${BLUE}===============================================${NC}"

# 1. Load configuration from .env if available
if [ -f .env ]; then
    echo "Loading configuration from .env..."
    export $(grep -v '^#' .env | xargs)
else
    echo -e "${YELLOW}Warning: .env file not found.${NC}"
    read -p "Enter GCP Project ID: " GCP_PROJECT
    read -p "Enter GCP Region [default: us-central1]: " GCP_REGION
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

# 2. Delete Cloud Run Services
echo -e "${BLUE}[1/3] Deleting Cloud Run services...${NC}"

if gcloud run services describe sre-agent --region "$GCP_REGION" &>/dev/null; then
    gcloud run services delete sre-agent --region "$GCP_REGION" --quiet
    echo -e "${GREEN}✓ Deleted Cloud Run service: sre-agent${NC}"
else
    echo "• Service 'sre-agent' does not exist."
fi

if gcloud run services describe sre-chaos-monkey --region "$GCP_REGION" &>/dev/null; then
    gcloud run services delete sre-chaos-monkey --region "$GCP_REGION" --quiet
    echo -e "${GREEN}✓ Deleted Cloud Run service: sre-chaos-monkey${NC}"
else
    echo "• Service 'sre-chaos-monkey' does not exist."
fi

# Delete Artifact Registry repository
REPO_NAME="sre-repo"
if gcloud artifacts repositories describe "$REPO_NAME" --location="$GCP_REGION" &>/dev/null; then
    gcloud artifacts repositories delete "$REPO_NAME" --location="$GCP_REGION" --quiet
    echo -e "${GREEN}✓ Deleted Artifact Registry repository: $REPO_NAME${NC}"
else
    echo "• Artifact Registry repository '$REPO_NAME' does not exist."
fi

# 3. Remove IAM Role Bindings & Service Accounts
echo -e "\n${BLUE}[2/3] Cleaning up IAM policies and Service Accounts...${NC}"

APP_SA_EMAIL="sre-chaos-monkey-sa@${GCP_PROJECT}.iam.gserviceaccount.com"
AGENT_SA_EMAIL="sre-agent-sa@${GCP_PROJECT}.iam.gserviceaccount.com"

# Target App SA Cleanup
if gcloud iam service-accounts describe "$APP_SA_EMAIL" &>/dev/null; then
    echo "Removing IAM policy bindings for SRE Chaos Monkey service account..."
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${APP_SA_EMAIL}" \
        --role="roles/cloudtrace.agent" &>/dev/null || true
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${APP_SA_EMAIL}" \
        --role="roles/logging.logWriter" &>/dev/null || true

    gcloud iam service-accounts delete "$APP_SA_EMAIL" --quiet
    echo -e "${GREEN}✓ Deleted service account: $APP_SA_EMAIL${NC}"
else
    echo "• Service account '$APP_SA_EMAIL' does not exist."
fi

# SRE Agent SA Cleanup
if gcloud iam service-accounts describe "$AGENT_SA_EMAIL" &>/dev/null; then
    echo "Removing IAM policy bindings for SRE agent service account..."
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/cloudtrace.user" &>/dev/null || true
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/logging.viewer" &>/dev/null || true

    gcloud iam service-accounts delete "$AGENT_SA_EMAIL" --quiet
    echo -e "${GREEN}✓ Deleted service account: $AGENT_SA_EMAIL${NC}"
else
    echo "• Service account '$AGENT_SA_EMAIL' does not exist."
fi

# SRE Build SA Cleanup
BUILD_SA_EMAIL="sre-build-sa@${GCP_PROJECT}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$BUILD_SA_EMAIL" &>/dev/null; then
    echo "Removing IAM policy bindings for SRE Build service account..."
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/logging.logWriter" &>/dev/null || true
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/storage.admin" &>/dev/null || true
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/run.admin" &>/dev/null || true
    gcloud projects remove-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/artifactregistry.writer" &>/dev/null || true

    gcloud iam service-accounts delete "$BUILD_SA_EMAIL" --quiet
    echo -e "${GREEN}✓ Deleted service account: $BUILD_SA_EMAIL${NC}"
else
    echo "• Service account '$BUILD_SA_EMAIL' does not exist."
fi

# 4. Optional local cleanup
echo -e "\n${BLUE}[3/3] Local cleanup...${NC}"
read -p "Would you like to delete the local .env and mock telemetry directories? (y/n): " CLEAN_LOCAL
if [[ "$CLEAN_LOCAL" =~ ^[Yy]$ ]]; then
    rm -f .env
    rm -rf mock_telemetry_data/
    echo -e "${GREEN}✓ Deleted local .env file and mock_telemetry_data/ folder.${NC}"
else
    echo "• Kept local configuration and mock telemetry data."
fi

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}      Demo Stack Resources Torn Down!          ${NC}"
echo -e "${GREEN}===============================================${NC}"
