# Change-impact demo — DevDNA's own backend

Request: `POST /api/projects/6d481a675e0a/impact/changed-files` with

```json
{"files": ["app/graph/engine.py", "app/services/change_impact.py", "docs/CHANGE_IMPACT.md", "a/app/api/experiments.py"]}
```

(The `a/` prefix on the last file simulates a raw git-diff path.)

## Summary

- Files mapped to analysed components: **3** / 4
- Affected components (all `possible`): **33** (corrected from 96 after a fix that stopped test nodes being double-counted as affected components)
- Tests predicted `may_break` (never `fail`): **63** of 246
- Changed files without test coverage: **0**
- Unmapped files (reported, not ignored): 1

## Notable output

- `app/services/change_impact.py` — direct dependents at distance 1 include
  `func:app.api.experiments.changed_files_impact_ep` (the endpoint that calls it).
- `app/graph/engine.py` — dependents include `func:app.services.analysis.load_graph`,
  `func:app.experiments.runner.compare` and `app.main.lifespan`; the whole API surface
  is reachable, hence 63 API tests flagged `may_break`.
- `docs/CHANGE_IMPACT.md` is honestly unmapped: *"no analysed component references
  this path (not analysed, non-code, or outside the analysed root)"*.
- No changed file lacked test coverage (`uncovered_files: []`) — the backend tests
  exercise all four code files.

## Former limitation, now fixed

This demo originally documented that whole-repo uploads with a nested `backend/`
prefix resolved no cross-module import edges (components keyed `backend.app.*`
while imports say `app.*`). The analyser now strips container directories above
the first package (a directory containing an `__init__.py`) from module names, so
such uploads resolve imports correctly and changed-file impact reports real
cross-file dependents. Regression tests: `backend/tests/test_nested_root.py`.

## Predictions are not proof

Every `possible`/`may_break` label above is a static-reachability prediction.
Turning one into an observation requires a controlled experiment
(`POST /api/predictions/{id}/experiments`).
