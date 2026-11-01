# Change-impact (Module 2)

Given a set of changed files (e.g. an open PR's diff), DevDNA predicts what the
change **may** affect: dependent components, the tests that exercise the
changed code, and — importantly — which changed files have **no covering
test at all**.

## Endpoint

`POST /api/projects/{project_id}/impact/changed-files`

```json
{"files": ["backend/app/graph/engine.py", "a/frontend/src/pages/Overview.jsx"]}
```

Response (abridged):

```json
{
  "changed_files": ["backend/app/graph/engine.py"],
  "unmapped_files": [],
  "affected": [{"id": "module:...", "certainty": "possible", "distance": 1, "path": [...]}],
  "tests_may_break": [{"nodeid": "tests/test_engine.py::test_x", "prediction": "may_break"}],
  "uncovered_files": ["backend/app/graph/engine.py"],
  "summary": {"files_mapped": 1, "files_unmapped": 0, "affected_total": 12,
               "tests_may_break": 3, "files_without_test_coverage": 1},
  "method": "Multi-source reverse breadth-first search over propagating edges ...",
  "limitations": ["Static reachability is not proof of breakage ..."]
}
```

## Honesty rules

- Every affected component is labelled `possible` and every test `may_break`.
  Static reachability is **not proof of breakage** — only a controlled
  experiment (`POST /predictions/{id}/experiments`) can turn a prediction
  into an observation.
- Files that no analysed component references are returned in
  `unmapped_files` with a reason, never silently ignored.
- A changed file with no test reaching it is listed in `uncovered_files` — a
  test-gap signal, not proof the code is untested at runtime.
- The response always carries `method` and `limitations`.

## Path matching

Paths are normalised before matching: git diff prefixes (`a/`, `b/`),
leading `./`, and Windows separators are stripped. Matching is by exact
relative file path against the analysed components — no substring guessing.

## Limits

- Input: 1–200 files per request (validated).
- Requires a completed analysis run (409 `not_analysed` otherwise).
- Read-only and deterministic: no prediction rows are stored, and identical
  input always yields an identical response.
