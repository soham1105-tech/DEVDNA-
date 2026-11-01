"""PR intelligence: one deterministic review package for a proposed change.

Combines the standing analysis with the deterministic modules built so far:

* change-impact (Module 2): what the changed files may affect;
* test-gap map (Module 3): whether the changed code is test-covered;
* findings: which recorded findings touch the changed files;
* reliability memory (Module 5): the project's experiment track record.

The package is a review aid, not a verdict: every number in it is a static
prediction or a recorded observation, labelled as such. AI narrative can be
requested separately through the existing AI chat endpoints with this
package as context.
"""
from __future__ import annotations

from app.services.change_impact import changed_files_impact, normalise_path
from app.services.reliability import reliability_memory
from app.services.test_gaps import compute_test_gaps
from sqlalchemy.orm import Session


def _findings_on_files(findings: list[dict], changed_norm: set[str]) -> list[dict]:
    # normalise the caller-supplied set too, so raw diff paths and Windows
    # separators match evidence file paths
    changed = {normalise_path(c) for c in changed_norm}
    matched = []
    for f in findings:
        for ev in f.get("evidence") or []:
            ffile = (ev.get("file") or "").replace("\\", "/").lstrip("/")
            if ffile and (ffile in changed or f"a/{ffile}" in changed):
                matched.append({"id": f.get("id"), "rule": f.get("rule"), "title": f.get("title"),
                                "severity": f.get("severity"), "file": ffile, "line": ev.get("line"),
                                "confidence": f.get("confidence"), "review_status": f.get("review_status")})
                break
    sev_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    matched.sort(key=lambda f: (sev_order.get(f.get("severity", "info"), 4), f.get("file", ""), f.get("line") or 0))
    return matched


def pr_review(db: Session, g, files: list[str], findings: list[dict],
              project_id: str, title: str | None = None) -> dict:
    impact = changed_files_impact(g, files)
    gaps = compute_test_gaps(g)
    changed_norm = {normalise_path(f) for f in files}
    gap_map = {e["file"]: e for e in gaps["file_gaps"]}
    coverage = []
    for f in sorted(changed_norm):
        e = gap_map.get(f)
        if e is None:
            coverage.append({"file": f, "status": "covered",
                             "note": "at least one test can reach this file's components"})
        else:
            coverage.append({"file": f, "status": "partially_uncovered" if e["covered"] else "uncovered",
                             "uncovered_components": e["uncovered"], "covered_components": e["covered"]})
    touching = _findings_on_files(findings, changed_norm)
    memory = reliability_memory(db, project_id)
    memory_excerpt = {
        "status": memory["status"],
        "observations": memory["summary"]["observations"],
        "experiments": memory["summary"]["experiments"],
        "note": memory.get("note") or ("Recorded experiment outcomes exist for this project; "
                                       "see /api/projects/{id}/reliability for the full memory."),
    }

    s = impact["summary"]
    summary = {
        "files": len(files),
        "files_mapped": s["files_mapped"],
        "files_unmapped": s["files_unmapped"],
        "affected_components": s["affected_total"],
        "tests_may_break": s["tests_may_break"],
        "changed_files_without_test_coverage": len([c for c in coverage if c["status"] != "covered"]),
        "findings_touching_changed_files": len(touching),
        "high_severity_findings": sum(1 for f in touching if f.get("severity") == "high"),
        "reliability_observations": memory_excerpt["observations"],
    }
    next_steps = []
    if summary["changed_files_without_test_coverage"]:
        next_steps.append("Add or extend tests for the changed files listed as uncovered/partially_uncovered "
                          "(test-gap signals, not proof of missing runtime coverage).")
    if summary["tests_may_break"]:
        next_steps.append("Run controlled experiments on the predictions to convert 'may_break' into "
                          "observed outcomes before merging.")
    if summary["findings_touching_changed_files"]:
        next_steps.append("Review the findings touching changed files; a proposed fix can be checked with "
                          "POST /api/projects/{id}/remediations/verify before applying it.")
    if summary["files_unmapped"]:
        next_steps.append("Some changed files are not in the analysed graph (unmapped) — confirm they are "
                          "intentional (docs, assets) or outside the analysed root.")
    if not next_steps:
        next_steps.append("No blockers flagged by the static package; this is not an approval — predictions "
                          "remain predictions until observed.")

    return {
        "title": title,
        "changed_files": impact["changed_files"],
        "unmapped_files": impact["unmapped_files"],
        "impact": {"affected": impact["affected"], "tests_may_break": impact["tests_may_break"],
                   "summary": s, "method": impact["method"]},
        "test_coverage": coverage,
        "findings": touching,
        "reliability": memory_excerpt,
        "summary": summary,
        "next_steps": next_steps,
        "method": ("Deterministic composition of change-impact (reverse BFS), the static test-gap map, "
                   "recorded findings of the latest run, and the project's reliability memory."),
        "limitations": [
            "This package is a review aid, not a verdict and not an approval; it contains no AI output.",
            "All impact and coverage entries are static predictions; only controlled experiments "
            "turn them into observations.",
            "Findings are matched by evidence file path; findings without a file location are not matched.",
            "The reliability excerpt is a summary — full memory at GET /api/projects/{id}/reliability.",
        ],
    }
