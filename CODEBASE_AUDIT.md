# Codebase Status

This page describes the implemented and locally verified scope of the repository. It is not a customer or market validation.

## What works (verified)

- **Static Analysis & Dependency Graph:** Backend parses Python (AST) and JavaScript/TypeScript (Tree-sitter) source code, builds a NetworkX `MultiDiGraph`, computes structural criticality, and persists results in SQLAlchemy (SQLite locally, Cloud SQL PostgreSQL in production with connection pooling).
- **Change Impact Intelligence:** Detects changed files and unified diffs, diffs dependency graphs, scores change risk across 6 explainable factors, prioritizes tests into 5 tiers, runs optional controlled validation, and generates evidence-backed merge recommendations.
- **Controlled Fault-Injection Experiments:** The bundled ShopLite reference project runs pytest fault-injection experiments in the local harness. Untrusted code requires Docker isolation locally, and in cloud API containers `DEVDNA_EXPERIMENTS_ENABLED=false` disables execution fail-closed so untrusted code never runs inside Cloud Run.
- **Production Hardening:** Opt-in `DEVDNA_API_TOKEN` auth (enforced on `/api/*` and OpenAPI schema routes), per-IP rate limiting, `/api/health` and `/api/ready` fail-closed readiness probes, `X-Request-ID` access logging, security headers, non-root Dockerfiles honoring `$PORT`, and complete GCP deployment configs (`firebase.json`, `deploy/cloudbuild.yaml`, `deploy/gcp-deploy.sh`, `deploy/gcp-deploy.ps1`, `docs/DEPLOYMENT.md`).
- **Optional Grounded AI:** Gemma/Gemini explanations are strictly optional; core static analysis, change impact, findings, and reporting work fully offline with no API key.

## Known limitations / not yet provisioned

- **Authentication & Multi-Tenancy:** Shared token auth (`DEVDNA_API_TOKEN`) is implemented; user accounts, RBAC, and multi-tenant isolation are not yet implemented.
- **Cloud Deployment:** Production GCP deployment artifacts and scripts are complete and verified against the codebase, but live provisioning on Google Cloud was not executed from this workstation because `gcloud` and `firebase` CLIs are not installed locally. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for exact commands and IAM roles.
- **Isolated Cloud Experiment Worker:** Untrusted experiment execution is safely disabled in Cloud Run (`DEVDNA_EXPERIMENTS_ENABLED=false`). Enabling untrusted cloud experiments requires a separate sandboxed Compute Engine or GKE worker fed by Cloud Tasks.
- **Durable Background Queue:** Local/single-instance analysis runs use FastAPI background tasks with stale-run recovery on restart; high-concurrency production deployments should offload long analyses to Cloud Tasks.

## Test verification

- **Backend (`backend/`):** Verified via `python -m pytest -q` (305 passed, 7 skipped — 1 Docker sandbox test when Docker daemon is unavailable, 6 Jest fixture tests when `backend/tests/fixtures/js_shop/node_modules` is not installed).
- **AST Engine (`ast-engine/`):** Verified via `python -m pytest -v` (8 passed).
- **Frontend (`frontend/`):** Verified via `npm test -- --run` (31 passed across 9 test files) and `npm run build` (Vite production bundle succeeds).
