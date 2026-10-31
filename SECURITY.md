# Security

## Deployment warning

By default the DevDNA API is unauthenticated and intended for localhost use only. Anyone who can reach the API can list and delete projects and, if a Gemini API key is configured, make requests that spend that key's quota. The server logs a warning on startup in this mode.

**Opt-in token auth:** set `DEVDNA_API_TOKEN` on the backend to require a shared secret. Every `/api` route except `/api/health` then demands an `X-DevDNA-Token` header with the exact value and answers `401 {"error": {"code": "unauthorized"}}` otherwise. In the frontend, paste the token in Settings under "API access token": it is kept in `sessionStorage` for the current tab only, sent on every API call, never displayed back, and never written to `localStorage`.

**Rate limiting:** every `/api` route (health included) is limited per client IP to `DEVDNA_RATE_LIMIT_PER_MINUTE` requests per minute (default 120). Over the budget the API answers `429 {"error": {"code": "rate_limited"}}`. The counter is in-process and resets on restart; with multiple uvicorn workers each worker keeps its own counter.

Do not treat the application as suitable for an untrusted or public network without also enabling TLS, strong token hygiene, and the operational protections a public deployment requires.

## Security scope

The codebase includes protections in several areas:

- **ZIP handling:** Uploaded archives are checked for unsafe paths and archive limits before project extraction.
- **Local-path gating:** Local project ingestion is restricted by configured allowed roots.
- **Experiment sandboxing:** Experiments use a restricted local harness for the bundled sample. Untrusted projects require the Docker sandbox; when Docker is unavailable, those experiments are blocked.

These are implementation details, not a guarantee that every attack path is prevented. The Docker isolation path has not been independently verified as secure. Static analysis of a repository does not make that repository safe to execute.

## Reporting a vulnerability

Please report security issues through a GitHub issue in this repository and include a concise description, impact, and reproduction steps. Avoid posting secrets or sensitive personal data.

**Private security contact:** `[Repository owner: add a private security contact email here]`

The placeholder above is intentionally not an email address. The repository owner should replace it with a private contact if one is available.
