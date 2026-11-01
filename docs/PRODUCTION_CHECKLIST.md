# Production deployment checklist

Everything that must be configured before DevDNA is exposed beyond localhost.
Each item is fail-closed: a missing variable either blocks startup or degrades
to a visibly limited mode, never to a silently unprotected deployment.

## 1. Backend API (FastAPI, port 8000)

| Variable | Required | Meaning |
| --- | --- | --- |
| `DEVDNA_API_TOKEN` | recommended | Shared secret. Every `/api` route except `/api/health` requires an `X-DevDNA-Token` header; the schema endpoints (`/docs`, `/openapi.json`, `/redoc`) are guarded too. Without it, the server refuses to start (see `DEVDNA_LOCAL_ONLY`). |
| `DEVDNA_LOCAL_ONLY` | yes, if no token | Explicit acknowledgement that this instance is unauthenticated and localhost-only. The Docker Compose demo sets it. |
| `DEVDNA_RATE_LIMIT_PER_MINUTE` | no | Per-IP request budget for `/api` routes (default 120). |
| `DEVDNA_MAX_QUEUED_EXPERIMENTS` | no | Server-wide cap on queued/running experiments (default 10); further starts answer 429. |
| `DEVDNA_GEMMA_MONTHLY_BUDGET` | no | Per-project Gemma requests per rolling 30 days (default 200). The guard fails closed: when the budget check itself fails, the AI call is blocked with a clear error. |
| `GEMINI_API_KEY` | optional | Enables Gemma explanations. Without it AI features answer 503 and everything else works. |

Paste the token into the frontend once (Settings → "API access token"); it is
kept in `sessionStorage` for the tab only.

## 2. Frontend (Vite build, port 8080 via nginx)

| Variable | Required | Meaning |
| --- | --- | --- |
| `VITE_API_URL` | yes, when the API is not same-origin | Base URL of the backend API. Leave unset when nginx/vercel proxies `/api` to the backend (same-origin). |
| `VITE_AST_ENGINE_URL` | yes, when the AST engine is not same-origin | Base URL of the AST engine. The Docker image defaults to `/ast` (proxied). |

Vite bakes these into the bundle at build time — set them as build args, not
runtime env.

### SPA fallback

The frontend uses `BrowserRouter`, so deep links (`/findings`, `/experiments/…`)
must serve `index.html`. Pick one:

**nginx** (used by `frontend/Dockerfile`):
```nginx
location / {
    root /usr/share/nginx/html;
    try_files $uri /index.html;
}
```

**vercel.json** (repo root):
```json
{ "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }] }
```

**netlify.toml** (repo root):
```toml
[[redirects]]
  from = "/*"
  to = "/index.html"
  status = 200
```

When the frontend and API are served from different origins, also check CORS:
the backend's `DEVDNA_CORS_ORIGINS` must list the frontend origin, and the
token header is already on the preflight allow-list (`X-DevDNA-Token`).

## 3. AST engine (port 8001)

| Variable | Required | Meaning |
| --- | --- | --- |
| `AST_ENGINE_API_TOKEN` | recommended | Opt-in token: `/analyze/repo` requires `X-DevDNA-Token`, 401 otherwise. The health route stays open. |
| `AST_ENGINE_RATE_LIMIT_PER_MINUTE` | no | Per-IP budget (default 60). |
| `AST_ENGINE_CORS_ORIGINS` | no | Comma-separated frontend origins allowed by CORS (local Vite origins are pre-allowed). |

GitHub fetch failures surface distinctly: 404 missing repo/branch, 429 GitHub
rate limit, 502 GitHub auth/server error, 504 GitHub timeout.

## 4. Docker Compose

`docker compose up --build` wires everything with same-origin proxies and
`DEVDNA_LOCAL_ONLY=true`. For anything beyond a demo: set a real
`DEVDNA_API_TOKEN` in the compose environment, remove `DEVDNA_LOCAL_ONLY`,
and terminate TLS at a reverse proxy.

## 5. Smoke test before exposing

1. `GET /api/health` without a token → 200.
2. `GET /api/projects` without a token → 401 `unauthorized`.
3. `GET /docs` without a token → 401 (schema does not leak).
4. `GET /api/projects` with `X-DevDNA-Token` → 200.
5. Load the frontend, open Settings, paste the token, run the demo analysis.

## 6. Runtime hardening (Module 7, always on)

These need no configuration — they are built into the backend:

- **Readiness is fail-closed.** `GET /api/health` answers 200 `{"status":"ok"}`
  only when the database responds; a database error answers **503**
  `{"status":"degraded"}` so a load balancer stops routing to a broken
  instance. The route stays token-exempt so probes work.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store` (API responses should never be cached or framed).
- **Request IDs.** Every response carries `X-Request-ID` (server-generated,
  16 hex chars; client-supplied values are ignored so nothing is echoed back).
  Every request writes one access-log line with method, path, status, duration
  and request ID — health probes are excluded to keep logs readable. Correlate
  a user report to a log line via the response's `X-Request-ID`.

## 7. Operations

- **Backups:** stop writes (or accept a moment of WAL churn) and copy
  `backend/data/devdna.db` together with `devdna.db-wal`/`-shm` if present.
  Projects' uploaded sources live under `backend/data/projects/` and are part
  of any complete backup.
- **Log rotation:** access logs go to the `devdna.access` logger; configure
  rotation in your process manager (e.g. uvicorn `--log-config` or a
  logrotate/copytruncate policy on the container's stdout collector).
- **WAL growth:** SQLite runs in WAL mode with a 30 s busy timeout; long-lived
  read connections checkpoint automatically. If the `-wal` file grows large
  after heavy imports, run `PRAGMA wal_checkpoint(TRUNCATE);` once during a
  quiet period.
- **Graceful restart:** a restart marks any queued/running analysis runs and
  experiments as failed with a re-run hint (they are never silently re-run or
  deleted) — re-trigger them after the restart.
