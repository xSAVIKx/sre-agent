#!/usr/bin/env bash
#
# deploy.sh - Least-privilege GCP Cloud Run Deployment Pipeline
#

set -euo pipefail

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}===============================================${NC}"
echo -e "${BLUE}   GCP SRE Agent Codelab Deployment Pipeline   ${NC}"
echo -e "${BLUE}===============================================${NC}"

# 1. Load configuration from .env
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env configuration file not found.${NC}"
    echo "Please run ./bootstrap.sh first to configure project settings."
    exit 1
fi

# Export env vars
export $(grep -v '^#' .env | xargs)

echo "Configuration loaded:"
echo "GCP Project: $GCP_PROJECT"
echo "GCP Region:  $GCP_REGION"
echo ""

# Set active project
gcloud config set project "$GCP_PROJECT"

# 2. Enable Google Cloud APIs
echo -e "${BLUE}[1/5] Enabling GCP APIs...${NC}"
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudtrace.googleapis.com \
    logging.googleapis.com

# 3. Create Service Accounts
echo -e "\n${BLUE}[2/5] Creating service accounts...${NC}"

# Target App Service Account
APP_SA_NAME="sre-target-app-sa"
APP_SA_EMAIL="${APP_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$APP_SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$APP_SA_NAME" \
        --description="Service account for the SRE Target FastAPI Application" \
        --display-name="SRE Target App Service Account"
    echo -e "${GREEN}✓ Created service account: $APP_SA_EMAIL${NC}"
else
    echo -e "${GREEN}✓ Service account already exists: $APP_SA_EMAIL${NC}"
fi

# SRE Agent Service Account
AGENT_SA_NAME="sre-agent-sa"
AGENT_SA_EMAIL="${AGENT_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$AGENT_SA_EMAIL" &>/dev/null; then
    gcloud iam service-accounts create "$AGENT_SA_NAME" \
        --description="Service account for SRE Agent (observability reader)" \
        --display-name="SRE Agent Service Account"
    echo -e "${GREEN}✓ Created service account: $AGENT_SA_EMAIL${NC}"
else
    echo -e "${GREEN}✓ Service account already exists: $AGENT_SA_EMAIL${NC}"
fi

# 4. Grant Least-Privilege IAM Roles
echo -e "\n${BLUE}[3/5] Assigning IAM roles (least-privilege)...${NC}"

# Target App Roles (Write-only telemetry)
echo "Assigning roles to target application service account..."
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/cloudtrace.agent" >/dev/null
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:${APP_SA_EMAIL}" \
    --role="roles/logging.logWriter" >/dev/null
echo -e "${GREEN}✓ Granted roles/cloudtrace.agent & roles/logging.logWriter to target app${NC}"

# SRE Agent Roles (Read-only telemetry)
echo "Assigning roles to SRE agent service account..."
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:${AGENT_SA_EMAIL}" \
    --role="roles/cloudtrace.user" >/dev/null
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:${AGENT_SA_EMAIL}" \
    --role="roles/logging.viewer" >/dev/null
echo -e "${GREEN}✓ Granted roles/cloudtrace.user & roles/logging.viewer to SRE Agent${NC}"

# 5. Build and Deploy Target Application
echo -e "\n${BLUE}[4/5] Building and deploying SRE Target FastAPI App...${NC}"
gcloud builds submit --tag "gcr.io/${GCP_PROJECT}/sre-target-app" -f app/Dockerfile .
gcloud run deploy sre-target-app \
    --image "gcr.io/${GCP_PROJECT}/sre-target-app" \
    --port 8080 \
    --service-account "$APP_SA_EMAIL" \
    --region "$GCP_REGION" \
    --allow-unauthenticated

TARGET_APP_URL=$(gcloud run services describe sre-target-app --region "$GCP_REGION" --format="value(status.url)")
echo -e "${GREEN}✓ Deployed target application to: $TARGET_APP_URL${NC}"

# 6. Build and Deploy SRE Agent
echo -e "\n${BLUE}[5/5] Building and deploying Cloud-Native SRE Agent...${NC}"
gcloud builds submit --tag "gcr.io/${GCP_PROJECT}/sre-agent" -f agent/Dockerfile .
gcloud run deploy sre-agent \
    --image "gcr.io/${GCP_PROJECT}/sre-agent" \
    --port 8080 \
    --service-account "$AGENT_SA_EMAIL" \
    --region "$GCP_REGION" \
    --set-env-vars "MOCK_GCP=false,GCP_PROJECT=${GCP_PROJECT},BACKEND_SERVICE_URL=${TARGET_APP_URL}" \
    --allow-unauthenticated

AGENT_URL=$(gcloud run services describe sre-agent --region "$GCP_REGION" --format="value(status.url)")
echo -e "${GREEN}✓ Deployed SRE Agent to: $AGENT_URL${NC}"

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}           Deployment Completed Successfully!  ${NC}"
echo -e "${GREEN}===============================================${NC}"
echo "Target App URL: $TARGET_APP_URL"
echo "SRE Agent URL:  $AGENT_URL"
echo ""
echo "Try running this command to trigger an error and start SRE diagnostics:"
echo -e "curl \"${TARGET_APP_URL}/api/gateway?trigger_error=true\""
echo -e "curl -X POST \"${AGENT_URL}/diagnose\" -H \"Content-Type: application/json\" -d '{\"prompt\": \"Gateway service is throwing errors. Find the root cause.\", \"project_id\": \"'\"$GCP_PROJECT\"'\"}'"
echo -e "${GREEN}===============================================${NC}"
