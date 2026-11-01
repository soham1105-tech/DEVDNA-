# Test-gap validation (Module 3)

Answers: **which analysed code has no test reaching it, and how much should
you trust the coverage map?**

## Endpoint

`GET /api/projects/{project_id}/test-gaps`

```json
{
  "test_count": 246,
  "file_gaps": [{"file": "app/cli.py", "components": 9, "covered": 3,
                  "uncovered": 6, "gap_ids": ["func:app.cli.build_report", "..."],
                  "min_distance": 1}],
  "fully_covered_files": ["app/db.py", "..."],
  "uncovered_by_type": {"function": 41, "module": 3},
  "summary": {"code_components": 812, "covered_components": 745,
               "uncovered_components": 67, "files_with_gaps": 12,
               "files_fully_covered": 96},
  "validation": {"experiments_observed": 0, "status": "static_only", "note": "..."},
  "validated_files": {},
  "method": "Multi-source forward breadth-first search over propagating edges ...",
  "limitations": ["Static reachability from a test is not proof the test asserts ..."]
}
```

## Two layers, kept honestly separate

1. **Static layer** — a multi-source forward BFS from every test node over
   propagating edges (`exercises`, `calls`, `handled_by`, …). Components no
   test can reach are *static gaps*, aggregated per file with the component
   ids that are unreachable. Infrastructure nodes (database, external_api,
   env_var, worker) are excluded from gap counts and reported separately in
   `uncovered_by_type`.
2. **Validation layer** — completed experiments record which tests actually
   executed (junit results in `experiment.observation.baseline.results`).
   Static coverage claims on files whose covering tests were *observed
   executing* are marked in `validated_files`; `validation.status` is
   `validated` or `static_only`.

## Honesty rules

- A **gap can never be validated** — absence of evidence is labelled as such.
- Static reachability from a test does not mean the test asserts meaningful
  behaviour of that component; it only shows the component lies on some path
  a test can execute.
- Dynamic dispatch and monkeypatching are not modelled, so gaps can be false
  positives and coverage can be false negatives.
- `method` and `limitations` are always present in the response.

## Relationship to change-impact (Module 2)

`changed-files` impact reports per-request `uncovered_files` for a specific
diff; this endpoint is the project-wide, standing version of the same signal.
