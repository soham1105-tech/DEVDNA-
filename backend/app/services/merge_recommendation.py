"""DevDNA Change Intelligence - Evidence-Backed Merge Recommendation.

Synthesizes graph diff, change risk, prioritized tests, findings, and controlled validation
into an actionable recommendation:
- APPROVE
- REVIEW REQUIRED
- BLOCK RECOMMENDED
"""
from __future__ import annotations

from typing import Any


def generate_merge_recommendation(
    risk: dict[str, Any],
    graph_diff: dict[str, Any],
    prioritized_tests: dict[str, Any],
    findings: list[dict[str, Any]],
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an evidence-backed merge recommendation."""
    risk_score = risk.get("score", 0)
    risk_level = risk.get("level", "low")
    critical_components = graph_diff.get("critical_components_affected", [])
    impacted_routes = graph_diff.get("impacted_routes", [])
    test_summary = prioritized_tests.get("summary", {})
    tier_1_tests = test_summary.get("tier_1_direct", 0)

    high_findings = [f for f in findings if f.get("severity") == "high"]
    med_findings = [f for f in findings if f.get("severity") == "medium"]

    evidence: list[dict[str, Any]] = []
    recommended_actions: list[str] = []
    blockers: list[str] = []

    # Evidence item 1: Risk score & blast radius
    evidence.append({
        "type": "risk_assessment",
        "title": f"Risk Score: {risk_score}/100 ({risk_level.upper()})",
        "description": f"Change impacts {graph_diff.get('summary', {}).get('directly_affected_count', 0)} directly and {graph_diff.get('summary', {}).get('transitively_affected_count', 0)} transitively affected component(s).",
    })

    # Evidence item 2: Critical routes
    if impacted_routes:
        evidence.append({
            "type": "route_exposure",
            "title": f"{len(impacted_routes)} API Route(s) Exposed",
            "description": f"Downstream routes impacted: {', '.join([r['route'] for r in impacted_routes[:3]])}{' and more' if len(impacted_routes) > 3 else ''}.",
        })

    # Evidence item 3: Static findings
    if high_findings:
        blockers.append(f"{len(high_findings)} high-severity finding(s) detected in changed code: '{high_findings[0].get('title', '')}'")
        evidence.append({
            "type": "finding_blocker",
            "title": "High Severity Static Finding",
            "description": high_findings[0].get("title", ""),
            "finding_id": high_findings[0].get("id"),
        })

    # Evidence item 4: Validation outcomes
    val_executed = validation and validation.get("executed", False)
    val_failed_tests = 0
    if val_executed:
        val_failed_tests = validation.get("faulted_summary", {}).get("failed", 0)
        evidence.append({
            "type": "empirical_validation",
            "title": "Controlled Fault Injection Outcome",
            "description": f"Empirical experiment triggered fault: {val_failed_tests} test(s) broke under fault conditions (accuracy: {validation.get('accuracy', 'N/A')}).",
        })
        if val_failed_tests > 0 and risk_score >= 65:
            blockers.append(f"Empirical validation confirmed breakage: {val_failed_tests} tests broke under controlled failure.")

    # Decision logic
    if high_findings or (risk_score >= 70 and (val_failed_tests > 0 or len(critical_components) >= 2)):
        decision = "BLOCK RECOMMENDED"
        severity = "high"
        headline = "High-risk changes with confirmed or high-severity failure propagation."
        if blockers:
            recommended_actions.append(f"Resolve blocking issue: {blockers[0]}")
        recommended_actions.append("Add fault-tolerant error boundaries or fallbacks before merging.")
        if tier_1_tests == 0:
            recommended_actions.append("Author Tier 1 tests directly covering the modified component.")
    elif risk_score >= 40 or (risk_score >= 30 and not val_executed) or tier_1_tests == 0 or med_findings or impacted_routes:
        decision = "REVIEW REQUIRED"
        severity = "medium"
        headline = "Meaningful risks, route exposure, or untested paths require engineering review."
        if tier_1_tests == 0:
            recommended_actions.append("Direct unit tests are missing for changed code; human review needed.")
        if impacted_routes:
            recommended_actions.append(f"Verify integration behavior for {len(impacted_routes)} exposed API route(s).")
        if not val_executed:
            recommended_actions.append("Controlled empirical validation was not run; verify in staging environment.")
    else:
        decision = "APPROVE"
        severity = "low"
        headline = "No significant risk identified within analysed scope; safe to proceed with normal merge."
        recommended_actions.append("Run existing CI suite and proceed with standard merge.")

    return {
        "decision": decision,
        "severity": severity,
        "headline": headline,
        "risk_score": risk_score,
        "blockers": blockers,
        "impacted_components_count": len(graph_diff.get("directly_affected", [])) + len(graph_diff.get("transitively_affected", [])),
        "impacted_routes_count": len(impacted_routes),
        "prioritized_tests_count": test_summary.get("total_prioritized_tests", 0),
        "evidence": evidence,
        "recommended_actions": recommended_actions,
        "limitations": [
            "Recommendations are advisory and based on static dependency reachability and controlled local validation.",
            "Dynamic runtime paths not captured by the AST engine may not be accounted for.",
        ],
    }
