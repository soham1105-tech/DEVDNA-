#!/usr/bin/env bash
# Reproducible Google Cloud deployment script for DevDNA.
# Usage:
#   export GCP_PROJECT_ID="your-project-id"
#   export GCP_REGION="us-central1"
#   ./deploy/gcp-deploy.sh
#
# Never hardcodes credentials, API keys, or deployment URLs.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-central1}"
AR_REPO="${DEVDNA_AR_REPO:-devdna}"
SQL_INSTANCE="${DEVDNA_SQL_INSTANCE:-devdna-pg}"
DB_NAME="${DEVDNA_DB_NAME:-devdna}"
DB_USER="${DEVDNA_DB_USER:-devdna_app}"
GCS_BUCKET="${DEVDNA_GCS_BUCKET:-${PROJECT_ID}-devdna-artifacts}"
SA_NAME="devdna-backend-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
FRONTEND_ORIGIN="${DEVDNA_FRONTEND_ORIGIN:-https://${PROJECT_ID}.web.app,https://${PROJECT_ID}.firebaseapp.com}"

echo "==> Configuring gcloud project: ${PROJECT_ID} (${REGION})"
gcloud config set project "${PROJECT_ID}"

echo "==> 1. Enabling required Google Cloud APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  cloudtasks.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com

echo "==> 2. Creating Artifact Registry repository (${AR_REPO})"
gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="DevDNA production container images"

echo "==> 3. Creating least-privilege Service Account (${SA_EMAIL})"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="DevDNA Backend Cloud Run Service Account"

for ROLE in \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/storage.objectAdmin \
  roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" --quiet >/dev/null
done

echo "==> 4. Provisioning Cloud Storage bucket (gs://${GCS_BUCKET})"
gcloud storage buckets describe "gs://${GCS_BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${GCS_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention

echo "==> 5. Provisioning Cloud SQL PostgreSQL (${SQL_INSTANCE}, db-f1-micro)"
if ! gcloud sql instances describe "${SQL_INSTANCE}" >/dev/null 2>&1; then
  gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-size=10GB \
    --storage-auto-increase \
    --backup-start-time=03:00
fi

gcloud sql databases describe "${DB_NAME}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1 || \
  gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}"

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)')"

echo "==> 6. Ensuring Secret Manager secrets exist"
ensure_secret() {
  local NAME="$1"
  local VALUE="$2"
  if ! gcloud secrets describe "${NAME}" >/dev/null 2>&1; then
    printf "%s" "${VALUE}" | gcloud secrets create "${NAME}" --data-file=- --replication-policy=automatic
  fi
}

if ! gcloud secrets describe devdna-api-token >/dev/null 2>&1; then
  GENERATED_TOKEN="$(openssl rand -hex 24)"
  ensure_secret devdna-api-token "${GENERATED_TOKEN}"
  echo "    Created secret devdna-api-token (retrieve with: gcloud secrets versions access latest --secret=devdna-api-token)"
fi

if ! gcloud secrets describe devdna-database-url >/dev/null 2>&1; then
  DB_PASS="$(openssl rand -hex 16)"
  gcloud sql users create "${DB_USER}" --instance="${SQL_INSTANCE}" --password="${DB_PASS}" 2>/dev/null || \
    gcloud sql users set-password "${DB_USER}" --instance="${SQL_INSTANCE}" --password="${DB_PASS}"
  DB_URL="postgresql+psycopg://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"
  ensure_secret devdna-database-url "${DB_URL}"
fi

if ! gcloud secrets describe devdna-gemini-api-key >/dev/null 2>&1; then
  ensure_secret devdna-gemini-api-key "${GEMINI_API_KEY:-}"
fi

echo "==> 7. Building, testing, and deploying Cloud Run services via Cloud Build"
SHORT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo manual)"
gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions="_REGION=${REGION},_AR_REPO=${AR_REPO},_CLOUD_SQL_INSTANCE=${CONNECTION_NAME},_GCS_BUCKET=${GCS_BUCKET},_CORS_ORIGINS=${FRONTEND_ORIGIN},_SERVICE_ACCOUNT=${SA_EMAIL},SHORT_SHA=${SHORT_SHA}"

echo "==> 8. Building and deploying frontend to Firebase Hosting"
(
  cd frontend
  npm ci
  VITE_AST_ENGINE_URL=/ast npm run build
)
firebase deploy --only hosting --project "${PROJECT_ID}"

BACKEND_URL="$(gcloud run services describe devdna-backend --region="${REGION}" --format='value(status.url)')"
AST_URL="$(gcloud run services describe devdna-ast-engine --region="${REGION}" --format='value(status.url)')"

echo ""
echo "==> Deployment complete!"
echo "    Frontend:   https://${PROJECT_ID}.web.app"
echo "    Backend:    ${BACKEND_URL}"
echo "    AST Engine: ${AST_URL}"
echo ""
echo "==> Verifying health endpoints:"
curl -fsS "${BACKEND_URL}/api/health" && echo ""
curl -fsS "${BACKEND_URL}/api/ready" && echo ""
curl -fsS "${AST_URL}/health" && echo ""
