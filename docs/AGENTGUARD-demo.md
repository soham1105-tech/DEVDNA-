# AgentGuard audit: ..

Scanned 11 config file(s), 109 record(s).
Findings: 1 (1 verified, 0 suspected).

## [MEDIUM] Third-party GitHub Action 'astral-sh/ruff-action' is not SHA-pinned
- Category: mcp_supply_chain | Status: **verified** | Confidence: high
  - Evidence: .github/workflows/ci.yml:0 — uses: astral-sh/ruff-action@v3 (pinned to a mutable ref)
  - Recommendation: Pin third-party actions to a full commit SHA.

## Limitations
- Static configuration analysis only: nothing was executed, so no finding is proof of exploitability.
- Only the documented config formats are parsed; agent configs in other shapes are reported as unsupported, never guessed.
- Path findings ('suspected') show that a permission chain exists in configuration, not that it was exercised at runtime.
- Secret values are never recorded; credential findings reference names and redacted placeholders only.
- First-party GitHub actions (actions/*, github/*, docker/*) are treated as trusted publishers.
