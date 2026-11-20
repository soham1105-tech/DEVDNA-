# Reproducible Google Cloud deployment script for DevDNA (Windows PowerShell).
# Usage:
#   .\deploy\gcp-deploy.ps1 -ProjectId "your-gcp-project-id" -Region "us-central1"
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "us-central1",
    [string]$ArRepo = "devdna",
    [string]$SqlInstance = "devdna-pg",
    [string]$DbName = "devdna",
    [string]$DbUser = "devdna_app"
)

$ErrorActionPreference = "Stop"

$GcsBucket = "$ProjectId-devdna-artifacts"
$SaName = "devdna-backend-sa"
$SaEmail = "$SaName@$ProjectId.iam.gserviceaccount.com"
$FrontendOrigin = "https://$ProjectId.web.app,https://$ProjectId.firebaseapp.com"

Write-Host "==> Setting active GCP project to $ProjectId ($Region)"
gcloud config set project $ProjectId

Write-Host "==> 1. Enabling required APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com cloudtasks.googleapis.com logging.googleapis.com monitoring.googleapis.com

Write-Host "==> 2. Ensuring Artifact Registry repository ($ArRepo)"
gcloud artifacts repositories describe $ArRepo --location=$Region 2>$null
if ($LASTEXITCODE -ne 0) {
    gcloud artifacts repositories create $ArRepo --repository-format=docker --location=$Region --description="DevDNA container images"
}

Write-Host "==> 3. Ensuring least-privilege Service Account ($SaEmail)"
gcloud iam service-accounts describe $SaEmail 2>$null
if ($LASTEXITCODE -ne 0) {
    gcloud iam service-accounts create $SaName --display-name="DevDNA Backend Cloud Run Service Account"
}
foreach ($role in @("roles/cloudsql.client", "roles/secretmanager.secretAccessor", "roles/storage.objectAdmin", "roles/logging.logWriter")) {
    gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$SaEmail" --role=$role --quiet | Out-Null
}

Write-Host "==> 4. Ensuring Cloud Storage bucket (gs://$GcsBucket)"
gcloud storage buckets describe "gs://$GcsBucket" 2>$null
if ($LASTEXITCODE -ne 0) {
    gcloud storage buckets create "gs://$GcsBucket" --location=$Region --uniform-bucket-level-access --public-access-prevention
}

Write-Host "==> 5. Ensuring Cloud SQL PostgreSQL ($SqlInstance)"
gcloud sql instances describe $SqlInstance 2>$null
if ($LASTEXITCODE -ne 0) {
    gcloud sql instances create $SqlInstance --database-version=POSTGRES_16 --tier=db-f1-micro --region=$Region --storage-size=10GB --storage-auto-increase --backup-start-time=03:00
}
gcloud sql databases describe $DbName --instance=$SqlInstance 2>$null
if ($LASTEXITCODE -ne 0) {
    gcloud sql databases create $DbName --instance=$SqlInstance
}
$ConnectionName = (gcloud sql instances describe $SqlInstance --format="value(connectionName)").Trim()

Write-Host "==> 6. Verifying Secret Manager secrets"
Write-Host "    Create secrets devdna-api-token, devdna-database-url, and devdna-gemini-api-key if not already created (see docs/DEPLOYMENT.md)."

Write-Host "==> 7. Running Cloud Build (tests + container build + Cloud Run deploy)"
$ShortSha = (git rev-parse --short HEAD).Trim()
gcloud builds submit --config deploy/cloudbuild.yaml --substitutions="_REGION=$Region,_AR_REPO=$ArRepo,_CLOUD_SQL_INSTANCE=$ConnectionName,_GCS_BUCKET=$GcsBucket,_CORS_ORIGINS=$FrontendOrigin,_SERVICE_ACCOUNT=$SaEmail,SHORT_SHA=$ShortSha"

Write-Host "==> 8. Building & deploying frontend to Firebase Hosting"
Push-Location frontend
try {
    npm ci
    $env:VITE_AST_ENGINE_URL = "/ast"
    npm run build
} finally {
    Pop-Location
}
firebase deploy --only hosting --project $ProjectId

Write-Host "==> Deployment complete! Verify https://$ProjectId.web.app and /api/health, /api/ready."
