# AgentGuard — AI-agent configuration security audit

AgentGuard is a deterministic, evidence-backed security audit of AI-agent
configurations in a repository. It parses explicitly documented config
formats, builds a permission graph, and reports findings labelled
**suspected** vs **verified**. Nothing is executed; the audit is static
configuration analysis only.

## Documented formats (one parser per format)

| Format | Files | Extracted |
| --- | --- | --- |
| `mcp_server_config` | `.mcp.json`, `mcp.json` (JSON with `mcpServers`) | server name, transport (stdio/http), command, args, package + pin status, env var names |
| `tool_settings` | `.claude/settings.json`, `.claude/settings.local.json` | `permissions.allow`/`deny` rules (`Tool(specifier)`), `defaultMode` |
| `agent_instructions` | `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, `AGENTS-*.md` | tool-permission bullets (`Tool(specifier)`), URLs with instruction verbs, curl-pipe patterns |
| `env_file` | `.env`, `.env.*` (not `.example`) | variable names + secret-shape classification; **values never stored** |
| `ci_workflow` | `.github/workflows/*.yml` | third-party `uses:` refs + SHA-pin status, `secrets.*` references, `run:` shell snippets, workflow permissions |
| `dependency_manifest` | `package.json` | dependency names + specs per section |

A file that matches one of these names but not its documented shape is
reported as **unsupported** with a reason — parsers never guess.

## Findings categories

Each finding carries evidence (file, line, redacted snippet), provenance
("static configuration analysis (no code executed)"), confidence, status
(verified = read directly from config; suspected = inferred risk), and a
recommendation.

| Category | Status | Trigger |
| --- | --- | --- |
| `excessive_permissions` | verified | unrestricted shell (`Bash(*)`), unrestricted filesystem write, unrestricted network, approval gate disabled (`defaultMode: bypassPermissions`) |
| `dangerous_tool_execution` | verified | destructive command prefixes allowlisted (`rm -rf`, `sudo`, `git push --force`); curl-pipe-to-shell in instructions |
| `credential_exposure` | verified | secret-shaped values committed in env files; secrets interpolated into shell commands; credential references |
| `mcp_supply_chain` | verified (cleartext HTTP, un-SHA-pinned third-party actions) / suspected (unpinned packages, remote endpoints) | MCP server configs and CI action refs |
| `prompt_injection` | suspected | instruction files direct the agent to load remote content (URL + instruction verb) |
| `data_exfiltration` | suspected | permission-graph path: untrusted input reaches an agent that holds a credential reference and can invoke an outbound channel |

Data-exfiltration findings state the required permission (the grant edges)
and the missing safeguard (no deny rule covers the channel). They never claim
the path was exploited.

## Redaction

Secret values are never recorded: env values are stored as `***` when
secret-shaped, credential nodes carry the variable name only, and every
evidence snippet passes through the shared redactor. Verified by test
(`test_secrets_are_never_recorded_in_findings_or_graph`).

## Usage

API (project-scoped, reads the project's stored source):
```
GET /api/projects/{project_id}/agentguard
```

CLI (any repository):
```
cd backend
python -m app.agentguard <repo-path> [--json out.json] [--markdown out.md]
```

## Limitations (also present in every API response)

- Static configuration analysis only: nothing was executed, so no finding is
  proof of exploitability.
- Only the documented config formats are parsed; agent configs in other
  shapes are reported as unsupported, never guessed.
- Path findings ("suspected") show that a permission chain exists in
  configuration, not that it was exercised at runtime.
- Secret values are never recorded; credential findings reference names and
  redacted placeholders only.
- First-party GitHub actions (`actions/*`, `github/*`, `docker/*`) are treated
  as trusted publishers.
- No LLM output is involved anywhere in this module: every finding is derived
  from a deterministic rule over parsed configuration.

## Acceptance

- Every category has at least one fixture repo that triggers it and one
  false-positive control repo that does not (`tests/test_agentguard_rules.py`).
- Demonstrated on a real repo: the audit of this repository (DevDNA itself)
  found its un-SHA-pinned `astral-sh/ruff-action@v3` in CI — see
  [AGENTGUARD-demo.md](AGENTGUARD-demo.md).
- Enforcement at an execution boundary is a later module; this one is
  read-only analysis.
