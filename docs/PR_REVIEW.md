# PR intelligence (Module 6)

One deterministic review package for a proposed change: what it may affect,
whether the changed code is test-covered, which recorded findings it touches,
and the project's experiment track record — composed from Modules 2, 3 and 5
plus the standing analysis.

## Endpoint

`POST /api/projects/{project_id}/pr-review`

```json
{"files": ["backend/app/graph/engine.py", "a/frontend/src/x.jsx"], "title": "My PR"}
```

Response sections:

- `impact` — change-impact result (Module 2): affected components (all
  `possible`), tests `may_break`, unmapped files;
- `test_coverage` — per changed file: `covered`, `partially_uncovered` or
  `uncovered` with component counts (Module 3 static gap map);
- `findings` — findings of the latest run whose evidence file is a changed
  file (sorted by severity);
- `reliability` — excerpt of the project's reliability memory (Module 5),
  including the honest `empty` state;
- `summary` — the roll-up numbers;
- `next_steps` — proposed actions derived only from what the package found;
- `method`, `limitations` — always present.

## Honesty rules

- The package is a **review aid, not a verdict and not an approval** — the
  response says so explicitly.
- No AI output; narrative can be requested via the existing AI chat endpoints
  with this package as context.
- All impact/coverage entries are static predictions; only controlled
  experiments turn them into observations.
- `next_steps` are derived mechanically from the package contents (uncovered
  files → add tests; `may_break` tests → run experiments; touching findings →
  consider remediation verification; unmapped files → confirm intentionality).
  With no signals, the next step says exactly that predictions remain
  predictions until observed.

## Uses

- Pre-merge triage: which of my tests might this PR break, and what should I
  run before merging?
- Review-context assembly for humans or AI assistants.
