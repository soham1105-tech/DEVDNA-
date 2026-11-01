# PR-intelligence demo — DevDNA's own backend

`POST /api/projects/6d481a675e0a/pr-review` with the same change set as the
change-impact demo:

```json
{"files": ["app/graph/engine.py", "app/services/change_impact.py", "a/app/api/experiments.py"],
 "title": "Example PR: extend change-impact engine"}
```

## Summary

- Files mapped: **3/3** (0 unmapped)
- Affected components (static, all `possible`): **33**
- Tests predicted `may_break` (never `fail`): **63**
- Changed files without full test coverage: **3**
- Findings touching changed files: **3** (high severity: 0)
- Reliability memory: **empty** (0 recorded observations)

## Test coverage of the changed files

- `app/api/experiments.py` — **partially_uncovered** (12 covered / 7 uncovered components)
- `app/graph/engine.py` — **partially_uncovered** (15 covered / 2 uncovered components)
- `app/services/change_impact.py` — **partially_uncovered** (2 covered / 1 uncovered components)

## Findings touching the changed files

- `untested_route` (low): Route GET /api/projects/{project_id}/predictions has no matching test — `app/api/experiments.py:143`
- `untested_route` (low): Route GET /api/predictions/{prediction_id} has no matching test — `app/api/experiments.py:154`
- `untested_route` (low): Route GET /api/calibration/stats has no matching test — `app/api/experiments.py:255`

## Next steps the package proposes

1. Add or extend tests for the changed files listed as uncovered/partially_uncovered (test-gap signals, not proof of missing runtime coverage).
2. Run controlled experiments on the predictions to convert 'may_break' into observed outcomes before merging.
3. Review the findings touching changed files; a proposed fix can be checked with POST /api/projects/{id}/remediations/verify before applying it.

## What this package is not

It is not an approval and contains no AI output. Every impact and coverage entry
is a static prediction; only controlled experiments turn predictions into
observations, and the empty reliability memory here says exactly that no such
observations exist yet for this project.
