# Test-gap demo — DevDNA's own backend

`GET /api/projects/6d481a675e0a/test-gaps` (project = DevDNA's `backend/` uploaded and analysed).

## Summary

- Test nodes in the graph: **246**
- Code components: **635** — covered **408**, uncovered **227**
- Files with gaps: **65**; fully covered: **0**
- Validation: **static_only** (no completed experiments on this project yet)

## Largest gaps

- `app/experiments/harness/devdna_faults.py` — 23/23 components unreachable from any test
- `app/ai/gemma.py` — 17/30 components unreachable from any test
- `app/schemas/__init__.py` — 12/12 components unreachable from any test
- `app/api/ai.py` — 9/25 components unreachable from any test
- `app/api/guard.py` — 8/10 components unreachable from any test
- `app/cli.py` — 8/8 components unreachable from any test

## Why the biggest gaps are believable

- `app/experiments/harness/devdna_faults.py` is a pytest *plugin* loaded via `-p devdna_faults`:
  no test imports it, so the static graph cannot see it. The gap report is honest about
  what the graph contains — and the limitation is stated in the response.
- `app/api/guard.py` (middleware) and `app/ai/gemma.py` are wired at import/startup time,
  which the propagation graph does not model (see limitations).
- `sample_project/shoplite/worker.py` is bundled fixture data, not backend code.

## These are claims, not verdicts

Uncovered means *no test can reach it in the static graph*. It does not mean the code is
dead or untested at runtime (dynamic dispatch, monkeypatching and import-time wiring are
not modelled), and covered does not mean well-asserted. Run a controlled experiment to
turn static claims into validated ones (`validation.status` flips from `static_only` to
`validated` for files whose covering tests were observed executing).
