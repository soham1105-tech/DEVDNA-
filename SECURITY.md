# Security

## Deployment warning

The DevDNA API currently has no authentication, authorization, or rate limiting. It is intended for localhost use only and must not be exposed publicly as-is. Anyone who can reach the API can list and delete projects and, if a Gemini API key is configured, make requests that spend that key's quota.

Do not treat the current application as suitable for an untrusted or public network. Keep it bound to localhost unless and until the repository owner implements and verifies appropriate access controls and operational protections.

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
