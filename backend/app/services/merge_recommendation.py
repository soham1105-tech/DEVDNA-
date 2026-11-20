"""DevDNA Change Intelligence - Evidence-Backed Merge Recommendation.

Synthesizes graph diff, change risk, prioritized tests, separated findings
(change-specific vs repository-wide), and controlled validation into an
internally consistent recommendation:
- APPROVE
- REVIEW REQUIRED
- BLOCK RECOMMENDED
"""
from __future__ import annotations

from typing import Any


def _finding_location(finding: dict[str, Any]) -> str:
    ev = finding.get("evidence") or []
    if ev and isinstance(ev[0], dict):
        f = ev[0].get("file") or ""
        ln = ev[0].get("line")
        if f and ln:
            return f"{f}:{ln}"
        if f:
            return f
    return finding.get("component_id") or "repository"


def generate_merge_recommendation(
    risk: dict[str, Any],
    graph_diff: dict[str, Any],
    prioritized_tests: dict[str, Any],
    findings: list[dict[str, Any]],
    validation: dict[str, Any] | None = None,
    *,
    repository_findings: list[dict[str, Any]] | None = None,
    unmapped_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate an evidence-backed merge recommendation.

    `findings` MUST contain only change-specific findings (touching changed or
    affected components). Pre-existing findings elsewhere in the repository are
    passed via `repository_findings` and surfaced separately as repository-wide
    warnings — never misattributed to the proposed diff.
    """
    risk_score = risk.get("score", 0)
    risk_level = risk.get("level", "low")
    diff_summary = graph_diff.get("summary", {})
    direct_count = diff_summary.get("directly_affected_count", 0)
    transitive_count = diff_summary.get("transitively_affected_count", 0)
    total_impacted = direct_count + transitive_count
    critical_components = graph_diff.get("critical_components_affected", [])
    impacted_routes = graph_diff.get("impacted_routes", [])
    test_summary = prioritized_tests.get("summary", {})
    tier_1_tests = test_summary.get("tier_1_direct", 0)

    change_high = [f for f in findings if f.get("severity") == "high"]
    change_med = [f for f in findings if f.get("severity") == "medium"]
    repo_all = repository_findings or []
    repo_high = [f for f in repo_all if f.get("severity") == "high"]
    unmapped = unmapped_files or []

    evidence: list[dict[str, Any]] = []
    recommended_actions: list[str] = []
    blockers: list[str] = []
    repository_warnings: list[dict[str, Any]] = []

    # Evidence item 1: Risk score & static blast radius
    evidence.append({
        "type": "risk_assessment",
        "provenance": "predicted",
        "title": f"Risk Score: {risk_score}/100 ({risk_level.upper()})",
        "description": (
            f"Static graph traversal found {direct_count} directly modified and "
            f"{transitive_count} transitively affected component(s)."
        ),
    })

    # Evidence item 2: Critical routes (predicted)
    if impacted_routes:
        evidence.append({
            "type": "route_exposure",
            "provenance": "predicted",
            "title": f"{len(impacted_routes)} API Route(s) Potentially Exposed",
            "description": (
                f"Downstream routes statically reachable: "
                f"{', '.join([r['route'] for r in impacted_routes[:3]])}"
                f"{' and more' if len(impacted_routes) > 3 else ''}."
            ),
        })

    # Evidence item 3: Change-specific static findings (only issues touching changed/affected code)
    if change_high:
        first_loc = _finding_location(change_high[0])
        blockers.append(
            f"{len(change_high)} high-severity change-specific finding(s) in affected code: "
            f"'{change_high[0].get('title', '')}' ({first_loc})"
        )
        evidence.append({
            "type": "finding_blocker",
            "provenance": "detected_statically",
            "scope": "change_specific",
            "title": f"High-Severity Change-Specific Finding ({first_loc})",
            "description": change_high[0].get("title", ""),
            "finding_id": change_high[0].get("id"),
        })

    # Separate pre-existing repository-wide high findings (NEVER merged into change blockers)
    for rf in repo_high[:5]:
        repository_warnings.append({
            "id": rf.get("id"),
            "title": rf.get("title", ""),
            "severity": rf.get("severity", "high"),
            "location": _finding_location(rf),
            "scope": "repository_wide",
            "note": "Pre-existing finding elsewhere in the repository (not attributed to this change).",
        })

    # Evidence item 4: Validation outcomes (experimentally confirmed only when executed)
    val_executed = bool(validation and validation.get("executed", False))
    val_failed_tests = 0
    if val_executed:
        val_failed_tests = validation.get("faulted_summary", {}).get("failed", 0)
        evidence.append({
            "type": "empirical_validation",
            "provenance": "experimentally_confirmed",
            "title": "Controlled Fault Injection Outcome",
            "description": (
                f"Executed experiment under '{validation.get('fault', {}).get('kind', 'fault')}': "
                f"{val_failed_tests} test(s) failed under fault conditions (accuracy: {validation.get('accuracy', 'N/A')})."
            ),
        })
        if val_failed_tests > 0 and risk_score >= 65:
            blockers.append(
                f"Controlled validation confirmed breakage: {val_failed_tests} test(s) failed under fault injection."
            )
    elif validation and validation.get("status") == "blocked":
        evidence.append({
            "type": "validation_blocked",
            "provenance": "unvalidated",
            "title": "Controlled Validation Blocked (No Code Executed)",
            "description": validation.get("reason", "Execution blocked by sandbox policy."),
        })

    # Decision logic — strictly aligned with evidence and risk score
    if change_high:
        decision = "BLOCK RECOMMENDED"
        severity = "high"
        headline = f"Blocked by {len(change_high)} high-severity static finding(s) in changed or affected code."
        recommended_actions.append(f"Resolve change-specific finding: {blockers[0]}")
        if tier_1_tests == 0 and total_impacted > 0:
            recommended_actions.append("Add Tier 1 unit tests covering the modified component.")
    elif risk_score >= 70 and val_executed and val_failed_tests > 0:
        decision = "BLOCK RECOMMENDED"
        severity = "high"
        headline = f"High-risk change ({risk_score}/100) with {val_failed_tests} experimentally confirmed test failure(s)."
        recommended_actions.append("Fix confirmed failure paths or add fault-tolerant error boundaries before merging.")
    elif risk_score >= 70 and len(critical_components) >= 2:
        decision = "BLOCK RECOMMENDED"
        severity = "high"
        blockers.append(
            f"High static risk ({risk_score}/100) spanning {len(critical_components)} critical component(s); "
            "not yet experimentally confirmed."
        )
        headline = f"High static risk ({risk_score}/100) across {len(critical_components)} critical components (unvalidated)."
        recommended_actions.append("Add error boundaries and run controlled validation or full integration tests before merging.")
    elif total_impacted == 0 and unmapped:
        decision = "REVIEW REQUIRED"
        severity = "medium"
        headline = (
            f"Incomplete impact mapping: {len(unmapped)} changed file(s) did not match components in this repository's analysed graph."
        )
        recommended_actions.append("Verify the changed file paths match files inside this repository and re-run analysis.")
    elif risk_score >= 40 or (total_impacted > 0 and tier_1_tests == 0) or change_med or impacted_routes:
        decision = "REVIEW REQUIRED"
        severity = "medium"
        headline = "Predicted impact, route exposure, or untested paths require engineering review."
        if total_impacted > 0 and tier_1_tests == 0:
            recommended_actions.append("Direct Tier 1 unit tests are missing for changed code; human review needed.")
        if impacted_routes:
            recommended_actions.append(f"Verify integration behavior for {len(impacted_routes)} potentially exposed API route(s).")
        if not val_executed:
            recommended_actions.append("Controlled empirical validation was not run; verify in staging or CI.")
    else:
        decision = "APPROVE"
        severity = "low"
        headline = "Low predicted risk within analysed static scope; no change-specific blockers detected."
        recommended_actions.append("Run existing CI suite and proceed with standard merge.")

    return {
        "decision": decision,
        "severity": severity,
        "headline": headline,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "blockers": blockers,
        "repository_warnings": repository_warnings,
        "repository_high_findings_count": len(repo_high),
        "change_high_findings_count": len(change_high),
        "impacted_components_count": total_impacted,
        "impacted_routes_count": len(impacted_routes),
        "prioritized_tests_count": test_summary.get("total_prioritized_tests", 0),
        "evidence": evidence,
        "recommended_actions": recommended_actions,
        "limitations": [
            "Static dependency reachability predicts potential impact, not confirmed runtime failure.",
            "Repository-wide findings are reported separately from change-specific blockers; line-level git blame attribution is unavailable without git history.",
        ],
    }
