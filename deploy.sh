#!/usr/bin/env bash
#
# deploy.sh - Least-privilege GCP Cloud Run Deployment Pipeline
#

set -euo pipefail

SKIP_INFRA=false

# Parse flags
for arg in "$@"; do
    case $arg in
        --skip-infra|-s)
        SKIP_INFRA=true
        shift
        ;;
    esac
done

# Add default Windows Google Cloud SDK path to PATH if present (Git Bash or WSL)
if [ -d "/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin" ]; then
    export PATH="/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin:$PATH"
elif [ -d "/mnt/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin" ]; then
    export PATH="/mnt/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin:$PATH"
fi

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
if [ "$SKIP_INFRA" = "true" ]; then
    echo "Mode:        Redeploy Only (Skipping Infra Setup)"
else
    echo "Mode:        Full Infrastructure Setup"
fi
echo ""

# Set active project
gcloud config set project "$GCP_PROJECT"

# Define service account names and emails (always needed for builds)
APP_SA_NAME="sre-chaos-monkey-sa"
APP_SA_EMAIL="${APP_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
AGENT_SA_NAME="sre-agent-sa"
AGENT_SA_EMAIL="${AGENT_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
INVENTORY_SA_NAME="inventory-agent-sa"
INVENTORY_SA_EMAIL="${INVENTORY_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
BUILD_SA_NAME="sre-build-sa"
BUILD_SA_EMAIL="${BUILD_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

if [ "$SKIP_INFRA" = "false" ]; then
    # 2. Enable Google Cloud APIs
    echo -e "${BLUE}[1/5] Enabling GCP APIs...${NC}"
    gcloud services enable \
        run.googleapis.com \
        cloudbuild.googleapis.com \
        cloudtrace.googleapis.com \
        logging.googleapis.com \
        monitoring.googleapis.com \
        artifactregistry.googleapis.com \
        firestore.googleapis.com \
        secretmanager.googleapis.com

    # Create GEMINI_API_KEY secret if it doesn't exist
    echo "Checking GEMINI_API_KEY secret in Secret Manager..."
    if [ -z "${GEMINI_API_KEY:-}" ]; then
        echo -e "${RED}Error: GEMINI_API_KEY is not defined in .env${NC}"
        exit 1
    fi

    if ! gcloud secrets describe GEMINI_API_KEY &>/dev/null; then
        gcloud secrets create GEMINI_API_KEY --replication-policy="automatic"
        echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
        echo -e "${GREEN}✓ Created secret GEMINI_API_KEY and added version 1${NC}"
    else
        echo -e "${GREEN}✓ Secret GEMINI_API_KEY already exists${NC}"
        # Always upload the current .env value as a new version to ensure it is up to date
        echo -n "$GEMINI_API_KEY" | gcloud secrets versions add GEMINI_API_KEY --data-file=-
        echo -e "${GREEN}✓ Added new version of GEMINI_API_KEY secret${NC}"
    fi

    # 3. Create Service Accounts
    echo -e "\n${BLUE}[2/5] Creating service accounts...${NC}"

    # Target App Service Account
    if ! gcloud iam service-accounts describe "$APP_SA_EMAIL" &>/dev/null; then
        gcloud iam service-accounts create "$APP_SA_NAME" \
            --description="Service account for SRE Chaos Monkey [demo=sre-agent-codelab]" \
            --display-name="SRE Chaos Monkey Service Account"
        echo -e "${GREEN}✓ Created service account: $APP_SA_EMAIL${NC}"
    else
        echo -e "${GREEN}✓ Service account already exists: $APP_SA_EMAIL${NC}"
    fi

    # SRE Agent Service Account
    if ! gcloud iam service-accounts describe "$AGENT_SA_EMAIL" &>/dev/null; then
        gcloud iam service-accounts create "$AGENT_SA_NAME" \
            --description="Service account for SRE Agent (observability reader) [demo=sre-agent-codelab]" \
            --display-name="SRE Agent Service Account"
        echo -e "${GREEN}✓ Created service account: $AGENT_SA_EMAIL${NC}"
    else
        echo -e "${GREEN}✓ Service account already exists: $AGENT_SA_EMAIL${NC}"
    fi

    # Inventory Agent Service Account
    if ! gcloud iam service-accounts describe "$INVENTORY_SA_EMAIL" &>/dev/null; then
        gcloud iam service-accounts create "$INVENTORY_SA_NAME" \
            --description="Service account for Inventory Agent and jobs [demo=sre-agent-codelab]" \
            --display-name="Inventory Agent Service Account"
        echo -e "${GREEN}✓ Created service account: $INVENTORY_SA_EMAIL${NC}"
    else
        echo -e "${GREEN}✓ Service account already exists: $INVENTORY_SA_EMAIL${NC}"
    fi

    # SRE Build Service Account (GCP Best Practice for Cloud Build)
    if ! gcloud iam service-accounts describe "$BUILD_SA_EMAIL" &>/dev/null; then
        gcloud iam service-accounts create "$BUILD_SA_NAME" \
            --description="Service account for Cloud Build [demo=sre-agent-codelab]" \
            --display-name="SRE Build Service Account"
        echo -e "${GREEN}✓ Created service account: $BUILD_SA_EMAIL${NC}"
    else
        echo -e "${GREEN}✓ Service account already exists: $BUILD_SA_EMAIL${NC}"
    fi

    # Wait for service account propagation to avoid IAM consistency failures
    echo "Waiting 10 seconds for service account creation to propagate..."
    sleep 10

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

    # SRE Agent Roles (Read-only telemetry & Firestore)
    echo "Assigning roles to SRE agent service account..."
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/cloudtrace.user" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/logging.viewer" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/monitoring.viewer" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/datastore.user" >/dev/null
    gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
        --member="serviceAccount:${AGENT_SA_EMAIL}" \
        --role="roles/secretmanager.secretAccessor" >/dev/null
    echo -e "${GREEN}✓ Granted roles/cloudtrace.user, roles/logging.viewer, roles/monitoring.viewer, roles/datastore.user & secretAccessor to SRE Agent${NC}"

    # SRE Build Roles (Least Privilege Cloud Build logging, storage, and deployment access)
    echo "Assigning roles to SRE Build service account..."
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/logging.logWriter" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/storage.admin" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/run.admin" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/artifactregistry.writer" >/dev/null
    gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/secretmanager.secretAccessor" >/dev/null
    echo -e "${GREEN}✓ Granted logging, storage, run admin, artifactregistry.writer & secretAccessor roles to SRE Build SA${NC}"

    # Inventory Agent Roles (Firestore access, running Cloud Run Jobs, logging)
    echo "Assigning roles to Inventory Agent service account..."
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${INVENTORY_SA_EMAIL}" \
        --role="roles/datastore.user" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${INVENTORY_SA_EMAIL}" \
        --role="roles/run.developer" >/dev/null
    gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
        --member="serviceAccount:${INVENTORY_SA_EMAIL}" \
        --role="roles/logging.logWriter" >/dev/null
    echo -e "${GREEN}✓ Granted roles/datastore.user, roles/run.developer & roles/logging.logWriter to Inventory Agent SA${NC}"

    # Allow SRE Build SA to act as the SRE application service accounts
    echo "Allowing SRE Build SA to act as application and agent service accounts..."
    gcloud iam service-accounts add-iam-policy-binding "$APP_SA_EMAIL" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/iam.serviceAccountUser" >/dev/null || true
    gcloud iam service-accounts add-iam-policy-binding "$AGENT_SA_EMAIL" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/iam.serviceAccountUser" >/dev/null || true
    gcloud iam service-accounts add-iam-policy-binding "$INVENTORY_SA_EMAIL" \
        --member="serviceAccount:${BUILD_SA_EMAIL}" \
        --role="roles/iam.serviceAccountUser" >/dev/null || true
    # Allow Inventory Agent SA to act as itself to execute jobs
    gcloud iam service-accounts add-iam-policy-binding "$INVENTORY_SA_EMAIL" \
        --member="serviceAccount:${INVENTORY_SA_EMAIL}" \
        --role="roles/iam.serviceAccountUser" >/dev/null || true
    echo -e "${GREEN}✓ Allowed Service Account User delegations${NC}"

    # Grant Service Account User to active gcloud account to run the build as the build SA
    ACTIVE_ACCOUNT=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null || true)
    if [ -n "$ACTIVE_ACCOUNT" ]; then
        if [[ "$ACTIVE_ACCOUNT" == *"gserviceaccount.com"* ]]; then
            MEMBER="serviceAccount:${ACTIVE_ACCOUNT}"
        else
            MEMBER="user:${ACTIVE_ACCOUNT}"
        fi
        echo "Granting roles/iam.serviceAccountUser to active deployer account ($ACTIVE_ACCOUNT)..."
        gcloud iam service-accounts add-iam-policy-binding "$BUILD_SA_EMAIL" \
            --member="$MEMBER" \
            --role="roles/iam.serviceAccountUser" >/dev/null || true
    fi

    # Create Artifact Registry repository for regional images if it doesn't exist
    echo "Checking SRE Artifact Registry repository in $GCP_REGION..."
    REPO_NAME="sre-repo"
    if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$GCP_REGION" &>/dev/null; then
        gcloud artifacts repositories create "$REPO_NAME" \
            --repository-format=docker \
            --location="$GCP_REGION" \
            --description="Docker repository for SRE Agent Codelab [demo=sre-agent-codelab]"
        echo -e "${GREEN}✓ Created Artifact Registry repository: $REPO_NAME in $GCP_REGION${NC}"
    else
        echo -e "${GREEN}✓ Artifact Registry repository already exists: $REPO_NAME in $GCP_REGION${NC}"
    fi

    # Create Firestore Native (default) database if it doesn't exist
    echo "Checking Firestore Native database (default)..."
    if ! gcloud firestore databases describe --database="(default)" &>/dev/null; then
        gcloud firestore databases create \
            --location="$GCP_REGION" \
            --type=firestore-native \
            --database="(default)" || true
        echo -e "${GREEN}✓ Initiated Firestore Native (default) database creation${NC}"
    else
        echo -e "${GREEN}✓ Firestore Native (default) database already exists${NC}"
    fi

    # Create composite index for itinerary_templates vector search
    echo "Creating Firestore Vector Index for itinerary_templates..."
    gcloud firestore indexes composite create \
        --collection-group=itinerary_templates \
        --query-scope=collection \
        --field-config=field-path=resource_type,order=ascending \
        --field-config=field-path=embedding,vector-config='{"dimension":"768","flat":{}}' --async || true
fi

# 5. Build and Deploy Target Application (SRE Chaos Monkey) - SKIPPED FOR FAST REDEPLOY
# echo -e "\n${BLUE}[4/5] Building and deploying SRE Chaos Monkey FastAPI App...${NC}"
# gcloud builds submit --config=app/cloudbuild.yaml \
#     --region="$GCP_REGION" \
#     --service-account="projects/${GCP_PROJECT}/serviceAccounts/${BUILD_SA_EMAIL}" \
#     --substitutions=_GCP_REGION="$GCP_REGION" .

TARGET_APP_URL=$(gcloud run services describe sre-chaos-monkey --region "$GCP_REGION" --format="value(status.url)")
TARGET_APP_URL=$(echo "$TARGET_APP_URL" | sed 's/.*http/http/')
echo -e "${GREEN}✓ SRE Chaos Monkey URL: $TARGET_APP_URL${NC}"

# 6. Build and Deploy Inventory Sub-Agent
echo -e "\n${BLUE}[5/7] Building and deploying Inventory Sub-Agent...${NC}"
gcloud builds submit --config=inventory_agent/cloudbuild.yaml \
    --region="$GCP_REGION" \
    --service-account="projects/${GCP_PROJECT}/serviceAccounts/${BUILD_SA_EMAIL}" \
    --substitutions=_GCP_REGION="$GCP_REGION" .

INVENTORY_AGENT_URL=$(gcloud run services describe inventory-agent --region "$GCP_REGION" --format="value(status.url)")
INVENTORY_AGENT_URL=$(echo "$INVENTORY_AGENT_URL" | sed 's/.*http/http/')
echo -e "${GREEN}✓ Deployed Inventory Sub-Agent to: $INVENTORY_AGENT_URL${NC}"

# Update Inventory Agent to set its own URL for callback injection
echo "Updating Inventory Agent environment variables with self URL..."
gcloud run services update inventory-agent \
    --region="$GCP_REGION" \
    --update-env-vars=INVENTORY_AGENT_URL="$INVENTORY_AGENT_URL" >/dev/null

# 7. Build and Deploy SRE Diagnostics Sub-Agent
echo -e "\n${BLUE}[6/7] Building and deploying SRE Diagnostics Sub-Agent...${NC}"
gcloud builds submit --config=sre_agent/cloudbuild.yaml \
    --region="$GCP_REGION" \
    --service-account="projects/${GCP_PROJECT}/serviceAccounts/${BUILD_SA_EMAIL}" \
    --substitutions=_GCP_REGION="$GCP_REGION",_INVENTORY_AGENT_URL="$INVENTORY_AGENT_URL" .

SRE_SUB_AGENT_URL=$(gcloud run services describe sre-sub-agent --region "$GCP_REGION" --format="value(status.url)")
SRE_SUB_AGENT_URL=$(echo "$SRE_SUB_AGENT_URL" | sed 's/.*http/http/')
echo -e "${GREEN}✓ Deployed SRE Diagnostics Sub-Agent to: $SRE_SUB_AGENT_URL${NC}"

# 8. Build and Deploy SRE Orchestrator Agent
echo -e "\n${BLUE}[7/7] Building and deploying SRE Orchestrator Agent...${NC}"
gcloud builds submit --config=agent/cloudbuild.yaml \
    --region="$GCP_REGION" \
    --service-account="projects/${GCP_PROJECT}/serviceAccounts/${BUILD_SA_EMAIL}" \
    --substitutions=_GCP_REGION="$GCP_REGION",_TARGET_APP_URL="$TARGET_APP_URL",_SRE_AGENT_URL="$SRE_SUB_AGENT_URL" .

AGENT_URL=$(gcloud run services describe sre-agent --region "$GCP_REGION" --format="value(status.url)")
AGENT_URL=$(echo "$AGENT_URL" | sed 's/.*http/http/')
echo -e "${GREEN}✓ Deployed SRE Orchestrator Agent to: $AGENT_URL${NC}"

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}           Deployment Completed Successfully!  ${NC}"
echo -e "${GREEN}===============================================${NC}"
echo "Target App URL:      $TARGET_APP_URL"
echo "Inventory Agent URL: $INVENTORY_AGENT_URL"
echo "SRE Sub-Agent URL:   $SRE_SUB_AGENT_URL"
echo "SRE Orchestrator:    $AGENT_URL"
echo ""
echo "Try running this command to trigger an error and start SRE diagnostics:"
echo -e "curl \"${TARGET_APP_URL}/api/gateway?trigger_error=true\""
echo -e "curl -X POST \"${AGENT_URL}/diagnose\" -H \"Content-Type: application/json\" -d '{\"prompt\": \"Gateway service is throwing errors. Find the root cause.\", \"project_id\": \"'\"$GCP_PROJECT\"'\"}'"
echo -e "${GREEN}===============================================${NC}"
