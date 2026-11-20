# DevDNA Production Deployment Guide (Google Cloud Platform)

This guide covers deploying DevDNA on Google Cloud Platform using **Firebase Hosting**, **Cloud Run**, **Cloud SQL (PostgreSQL 16)**, **Cloud Storage**, **Secret Manager**, **Artifact Registry**, and **Cloud Build**.

---

## 1. Architecture Overview

```text
Browser (HTTPS)
   │
   ▼
Firebase Hosting (React/Vite SPA + immutable /assets caching)
   ├── /api/** , /docs , /openapi.json  ──► Cloud Run: devdna-backend (FastAPI, non-root)
   │                                           ├── Cloud SQL PostgreSQL 16 (Unix socket, pooled)
   │                                           ├── Cloud Storage (persistent uploads & reports)
   │                                           ├── Secret Manager (API token, DB URL, Gemini key)
   │                                           └── DEVDNA_EXPERIMENTS_ENABLED=false (fail-closed)
   ├── /ast/**                          ──► Cloud Run: devdna-ast-engine (FastAPI, non-root)
   └── /**                              ──► /index.html (SPA fallback)
```

### Security Boundary for Untrusted Experiments
- **Never** execute untrusted user repositories inside the public Cloud Run API container, and **never** mount a host Docker socket into Cloud Run.
- In production Cloud Run, `DEVDNA_EXPERIMENTS_ENABLED=false` is enforced. Static architecture analysis, Change Impact Intelligence, risk scoring, test prioritization, findings, AgentGuard, and PR reviews all run normally. Experiment execution returns a clear `blocked` state explaining why no untrusted code was executed.
- **To enable untrusted experiment execution in production**, deploy a dedicated isolated worker (e.g., a locked-down Compute Engine VM with gVisor/Docker or a GKE Autopilot Sandbox pod pool) fed by **Cloud Tasks**:
  - Dedicated worker service account with zero Secret Manager / Cloud SQL credentials.
  - `--network=none`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, CPU/memory/PID limits, and hard timeouts.
  - Ephemeral temp workspace destroyed immediately after each run.

---

## 2. Prerequisites & Required IAM Permissions

Install and authenticate the CLIs:
- [Google Cloud CLI (`gcloud`)](https://cloud.google.com/sdk/docs/install)
- [Firebase CLI (`firebase`)](https://firebase.google.com/docs/cli)

Required IAM roles for the deploying operator:
- `roles/serviceusage.serviceUsageAdmin` (enable APIs)
- `roles/artifactregistry.admin` (create Docker repository)
- `roles/iam.serviceAccountAdmin` & `roles/resourcemanager.projectIamAdmin` (create least-privilege runtime service account)
- `roles/cloudsql.admin` (provision PostgreSQL instance & database)
- `roles/storage.admin` (create GCS artifacts bucket)
- `roles/secretmanager.admin` (create secrets)
- `roles/run.admin` & `roles/cloudbuild.builds.editor` (build & deploy Cloud Run)
- Firebase Hosting deploy permissions (`firebase login`)

---

## 3. One-Command Deployment

### Linux / macOS
```bash
gcloud auth login
firebase login
export GCP_PROJECT_ID="your-gcp-project-id"
export GCP_REGION="us-central1"
# Optional: export GEMINI_API_KEY="your-key"
./deploy/gcp-deploy.sh
```

### Windows (PowerShell)
```powershell
gcloud auth login
firebase login
.\deploy\gcp-deploy.ps1 -ProjectId "your-gcp-project-id" -Region "us-central1"
```

---

## 4. Environment Variable Reference

| Variable | Service | Required (Prod) | Description |
| --- | --- | --- | --- |
| `DEVDNA_API_TOKEN` | Backend | **Yes** | Shared secret required in `X-DevDNA-Token` on all `/api/*` routes (except `/api/health`, `/api/ready`) and `/docs`, `/openapi.json`, `/redoc`. Stored in Secret Manager. |
| `DEVDNA_LOCAL_ONLY` | Backend | No (unset in prod) | Only for local unauthenticated dev (`true`). Omit in production so startup fails closed if `DEVDNA_API_TOKEN` is missing. |
| `DEVDNA_DATABASE_URL` | Backend | **Yes** | SQLAlchemy PostgreSQL URL (`postgresql+psycopg://USER:PASS@/devdna?host=/cloudsql/PROJECT:REGION:INSTANCE`). Stored in Secret Manager. |
| `DEVDNA_EXPERIMENTS_ENABLED` | Backend | **Yes (`false`)** | Disables in-container experiment execution on Cloud Run. |
| `DEVDNA_CORS_ORIGINS` | Backend | **Yes** | Comma-separated allowed origins (e.g. `https://<project>.web.app`). |
| `DEVDNA_GCS_BUCKET` | Backend | Recommended | Cloud Storage bucket for persistent uploads & reports. |
| `DEVDNA_RATE_LIMIT_PER_MINUTE` | Backend | No (default `120`) | Per-IP sliding-window request limit. |
| `DEVDNA_MAX_QUEUED_EXPERIMENTS` | Backend | No (default `10`) | Maximum concurrent queued/running experiments. |
| `DEVDNA_GEMMA_MONTHLY_BUDGET` | Backend | No (default `200`) | Rolling 30-day per-project AI request budget (fails closed). |
| `GEMINI_API_KEY` | Backend | Optional | Enables grounded Gemma/Gemini explanations. Without it, all deterministic analysis continues working. |
| `AST_ENGINE_API_TOKEN` | AST Engine | Recommended | Shared token for `/analyze/repo` (same secret as `DEVDNA_API_TOKEN`). |
| `AST_ENGINE_CORS_ORIGINS` | AST Engine | **Yes** | Allowed frontend origins. |
| `VITE_API_URL` | Frontend (build) | Empty (same-origin) | Leave unset when using Firebase Hosting rewrites. |
| `VITE_AST_ENGINE_URL` | Frontend (build) | `/ast` | Proxied to `devdna-ast-engine` via Firebase Hosting rewrites. |

---

## 5. Google Cloud Resource Inventory & Expected Monthly Cost

| Resource | Configuration | Estimated Cost (Low-Traffic / Demo) |
| --- | --- | --- |
| Firebase Hosting | Spark / Blaze static assets + rewrites | Free tier (10 GB storage, 360 MB/day transfer) |
| Cloud Run (`devdna-backend`) | `min-instances=0`, `max-instances=4`, 1 vCPU, 1 GiB | \$0–\$5 / mo (scales to zero when idle) |
| Cloud Run (`devdna-ast-engine`) | `min-instances=0`, `max-instances=2`, 1 vCPU, 512 MiB | \$0–\$2 / mo (scales to zero when idle) |
| Cloud SQL PostgreSQL 16 | `db-f1-micro`, 10 GB SSD, automated backups | ~\$9–\$12 / mo |
| Cloud Storage | Standard, single-region, uniform bucket-level access | < \$1 / mo |
| Artifact Registry & Secret Manager | 2 images, 3 secrets | < \$1 / mo |
| **Total** | | **~\$10–\$20 / month** |

---

## 6. Post-Deployment Verification

```bash
# 1. Health & readiness probes (token-exempt)
curl -fsS https://YOUR_PROJECT.web.app/api/health
curl -fsS https://YOUR_PROJECT.web.app/api/ready

# 2. Verify auth fails closed without token (must return 401)
curl -i https://YOUR_PROJECT.web.app/api/projects

# 3. Verify authenticated request succeeds
TOKEN="$(gcloud secrets versions access latest --secret=devdna-api-token)"
curl -fsS -H "X-DevDNA-Token: ${TOKEN}" https://YOUR_PROJECT.web.app/api/projects
```

---

## 7. Rollback Instructions

### Cloud Run (instant traffic rollback to previous revision)
```bash
gcloud run revisions list --service=devdna-backend --region=us-central1
gcloud run services update-traffic devdna-backend \
  --region=us-central1 \
  --to-revisions=PREVIOUS_REVISION_NAME=100
```

### Firebase Hosting
```bash
firebase hosting:rollback --project YOUR_PROJECT_ID
```

### Cloud SQL Database
Restore from automated daily backups in the Cloud Console or via `gcloud sql backups restore`.

---

## 8. Troubleshooting

- **Backend returns 503 on `/api/ready` or `/api/health`**: Check Cloud SQL socket attachment (`--add-cloudsql-instances`), verify `devdna-database-url` secret formatting, and check Cloud Run logs filtered by `X-Request-ID`.
- **Frontend returns 401**: Open **Settings → API access token** in the UI and paste the value of `devdna-api-token` (stored in `sessionStorage` for the tab only).
- **Experiments show `blocked` in Cloud Run**: Expected behavior when `DEVDNA_EXPERIMENTS_ENABLED=false` to prevent untrusted code execution in the API container.
