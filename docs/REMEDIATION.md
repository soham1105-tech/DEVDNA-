# Verified remediation (Module 4)

Check a proposed patch **before** anyone applies it: DevDNA writes the patch
to a disposable copy of the project, re-runs the full analysis pipeline on
that copy, and reports which findings disappear — with honest labels about
what "verified" means here.

## Endpoint

`POST /api/projects/{project_id}/remediations/verify`

```json
{
  "files": {"app/services/shipping.py": "<full replacement contents>"},
  "finding_ids": ["<optional; empty = all findings of the latest run>"]
}
```

Response (abridged):

```json
{
  "patched_files": ["app/services/shipping.py"],
  "findings": [{"finding_id": "...", "rule": "http_without_timeout",
                 "status": "resolved_static"}],
  "summary": {"checked": 12, "resolved_static": 3, "still_present": 9,
               "introduced": 1, "patched_components": 812},
  "introduced_findings": [{"rule": "swallowed_exception", "title": "..."}],
  "method": "The proposed file contents were written to a disposable copy ...",
  "limitations": ["'resolved_static' means the finding's fingerprint is absent ..."]
}
```

## What "verified" means — and does not mean

- `resolved_static`: the finding's **fingerprint** (the same definition used
  for suppression tracking) is absent from the patched analysis. It is *not*
  proof the remediation is correct or complete at runtime.
- `still_present`: the fingerprint still appears.
- `introduced_findings`: findings that appear **only** in the patched
  analysis. A fix that relocates a pattern can read as one resolved plus one
  introduced; both are reported.
- **No tests are executed.** Combine with a controlled experiment
  (`POST /predictions/{id}/experiments`) for behavioural evidence.
- The original project source is never modified; the patched copy is deleted
  after verification. Nothing is persisted.

## Input limits

- 1–20 files per request, each ≤ 200 KB, full replacement contents
  (unambiguous, unlike diffs).
- Paths must be repo-relative and stay inside the project; absolute paths,
  drive-anchored paths and `..` traversal are rejected.
- Requires a completed analysis run (409 otherwise).

## Relationship to other modules

- Findings come from the standing analysis (Module 0 pipeline).
- `resolved_static` is a static claim, like `possible`/`may_break` in
  Modules 2–3; only experiments turn predictions into observations.
