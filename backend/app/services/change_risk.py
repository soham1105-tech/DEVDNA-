"""DevDNA Change Intelligence - Change Risk Intelligence Service.

Computes a transparent, explainable 0-100 risk score for a code change.
Combines:
- Dependency blast radius (25%)
- Component criticality (20%)
- Impacted API route exposure (20%)
- Test coverage gaps over affected code (15%)
- Severity of findings touching changed code (10%)
- Historical reliability evidence from past experiments (10%)
- Explicit uncertainty quantification (unmapped files, dynamic calls).
"""
from __future__ import annotations

from typing import Any

from app.services.reliability import reliability_memory
from sqlalchemy.orm import Session


def calculate_change_risk(
    db: Session,
    project_id: str,
    graph_diff: dict[str, Any],
    findings: list[dict[str, Any]],
    unmapped_files: list[dict[str, Any]],
    total_routes_in_repo: int = 1,
) -> dict[str, Any]:
    """Calculate an explainable change risk score and breakdown."""
    summary = graph_diff.get("summary", {})
    directly = graph_diff.get("directly_affected", [])
    transitively = graph_diff.get("transitively_affected", [])
    potentially = graph_diff.get("potentially_affected", [])
    critical = graph_diff.get("critical_components_affected", [])
    routes = graph_diff.get("impacted_routes", [])
    tests = graph_diff.get("associated_tests", [])

    total_affected = len(directly) + len(transitively) + len(potentially)
    total_nodes = summary.get("total_nodes_modified", 1) or 1

    # 1. Blast Radius Factor (Weight: 0.25)
    # Ratio of affected components to total graph size, scaled
    ratio = total_affected / max(total_nodes, 1)
    if ratio > 0.4:
        blast_score = 100.0
    elif ratio > 0.2:
        blast_score = 75.0 + (ratio - 0.2) * 125.0
    elif ratio > 0.05:
        blast_score = 40.0 + (ratio - 0.05) * 233.3
    else:
        blast_score = min(40.0, ratio * 800.0)
    blast_score = round(min(100.0, max(0.0, blast_score)), 1)
    blast_reason = f"Affects {total_affected} component(s) ({round(ratio * 100, 1)}% of analysed system)."

    # 2. Component Criticality Factor (Weight: 0.20)
    # Checks if modified or affected components touch core hubs, db, auth, or have high fan-in
    crit_count = len(critical)
    if any(c.get("distance") == 0 and c.get("is_critical") for c in directly):
        crit_score = 90.0 + min(10.0, crit_count * 2.0)
        crit_reason = f"Directly alters critical component ({critical[0].get('critical_reason', 'core subsystem')})."
    elif crit_count > 0:
        crit_score = min(85.0, 45.0 + crit_count * 8.0)
        crit_reason = f"Reaches {crit_count} downstream critical component(s) including {critical[0].get('name')}."
    else:
        crit_score = 15.0
        crit_reason = "No critical hubs or core subsystem components in reachability path."
    crit_score = round(min(100.0, max(0.0, crit_score)), 1)

    # 3. Route Exposure Factor (Weight: 0.20)
    route_count = len(routes)
    route_ratio = route_count / max(total_routes_in_repo, 1)
    if route_count >= 5 or route_ratio > 0.5:
        route_score = 95.0
        route_reason = f"High route exposure: {route_count} API route(s) in blast radius."
    elif route_count >= 1:
        route_score = 50.0 + min(40.0, route_count * 12.0)
        route_reason = f"{route_count} user/client-facing API route(s) potentially affected."
    else:
        route_score = 10.0
        route_reason = "No external API routes reachable downstream from changed components."
    route_score = round(min(100.0, max(0.0, route_score)), 1)

    # 4. Test Coverage Gap Factor (Weight: 0.15)
    # Compares associated tests with affected components
    test_count = len(tests)
    if total_affected == 0:
        coverage_score = 10.0
        coverage_reason = "No components in the analysed graph were affected by the specified files."
    elif total_affected > 0 and test_count == 0:
        coverage_score = 95.0
        coverage_reason = "Zero covering tests identified for any affected components (severe blind spot)."
    elif test_count < (total_affected / 2):
        coverage_score = 65.0
        coverage_reason = f"Limited test coverage: only {test_count} test(s) cover {total_affected} affected component(s)."
    else:
        coverage_score = max(10.0, 40.0 - min(30.0, test_count * 3.0))
        coverage_reason = f"Active test coverage: {test_count} test(s) exercise components in the propagation path."
    coverage_score = round(min(100.0, max(0.0, coverage_score)), 1)

    # 5. Finding Severity Factor (Weight: 0.10)
    # Severity of static findings specifically touching changed/affected components
    high_findings = [f for f in findings if f.get("severity") == "high"]
    med_findings = [f for f in findings if f.get("severity") == "medium"]
    if high_findings:
        finding_score = min(100.0, 75.0 + len(high_findings) * 10.0)
        finding_reason = f"{len(high_findings)} high-severity change-specific finding(s) in affected code ({high_findings[0].get('title', '')[:50]})."
    elif med_findings:
        finding_score = min(60.0, 30.0 + len(med_findings) * 8.0)
        finding_reason = f"{len(med_findings)} medium-severity change-specific finding(s) in affected code."
    else:
        finding_score = 10.0
        finding_reason = "No high or medium severity findings touching changed or affected components."
    finding_score = round(min(100.0, max(0.0, finding_score)), 1)

    # 6. Historical Reliability Memory Factor (Weight: 0.10)
    memory = reliability_memory(db, project_id)
    fail_rate = 0.0
    mem_exps = memory.get("summary", {}).get("experiments", 0)
    if mem_exps > 0:
        # Check if previous experiments showed failures
        obs = memory.get("observations", [])
        failed_obs = sum(1 for o in obs if o.get("observed") in ("failed", "error"))
        fail_rate = failed_obs / max(len(obs), 1)
        history_score = round(min(100.0, 20.0 + fail_rate * 80.0), 1)
        history_reason = f"Historical track record shows {round(fail_rate * 100, 1)}% failure rate across {mem_exps} previous experiment(s)."
    else:
        history_score = 30.0
        history_reason = "No previous experiments recorded for this project (uncalibrated baseline)."

    # 7. Uncertainty Quantification
    unmapped_count = len(unmapped_files)
    uncertain_score = 0
    uncertainty_notes = []
    if unmapped_count > 0:
        uncertain_score += min(40, unmapped_count * 10)
        uncertainty_notes.append(f"{unmapped_count} changed file(s) could not be mapped to the dependency graph.")
    if len(potentially) > 0:
        uncertain_score += min(30, len(potentially) * 5)
        uncertainty_notes.append(f"{len(potentially)} component(s) have mocked or conditional dependency paths.")
    uncertainty_notes.append("Static analysis reachability indicates potential, not guaranteed runtime failure.")
    uncertain_score = round(min(100.0, uncertain_score), 1)

    # Calculate overall weighted score
    weights = {
        "blast_radius": 0.25,
        "component_criticality": 0.20,
        "route_exposure": 0.20,
        "test_coverage": 0.15,
        "finding_severity": 0.10,
        "historical_reliability": 0.10,
    }

    factors = [
        {"name": "Dependency Blast Radius", "key": "blast_radius", "weight": weights["blast_radius"],
         "raw_score": blast_score, "weighted_score": round(blast_score * weights["blast_radius"], 2), "reason": blast_reason},
        {"name": "Component Criticality", "key": "component_criticality", "weight": weights["component_criticality"],
         "raw_score": crit_score, "weighted_score": round(crit_score * weights["component_criticality"], 2), "reason": crit_reason},
        {"name": "API Route Exposure", "key": "route_exposure", "weight": weights["route_exposure"],
         "raw_score": route_score, "weighted_score": round(route_score * weights["route_exposure"], 2), "reason": route_reason},
        {"name": "Test Coverage Gap", "key": "test_coverage", "weight": weights["test_coverage"],
         "raw_score": coverage_score, "weighted_score": round(coverage_score * weights["test_coverage"], 2), "reason": coverage_reason},
        {"name": "Static Finding Severity", "key": "finding_severity", "weight": weights["finding_severity"],
         "raw_score": finding_score, "weighted_score": round(finding_score * weights["finding_severity"], 2), "reason": finding_reason},
        {"name": "Historical Reliability", "key": "historical_reliability", "weight": weights["historical_reliability"],
         "raw_score": history_score, "weighted_score": round(history_score * weights["historical_reliability"], 2), "reason": history_reason},
    ]

    total_weighted = sum(f["weighted_score"] for f in factors)
    final_score = int(round(total_weighted))
    final_score = min(100, max(0, final_score))

    if final_score >= 85:
        level = "critical"
    elif final_score >= 70:
        level = "high"
    elif final_score >= 40:
        level = "medium"
    else:
        level = "low"

    return {
        "score": final_score,
        "level": level,
        "scale": "0-100",
        "formula": "weighted sum: 25% blast radius + 20% criticality + 20% route exposure + 15% test gap + 10% change-specific findings + 10% reliability history",
        "factors": factors,
        "uncertainty": {
            "score": uncertain_score,
            "level": "high" if uncertain_score >= 50 else ("medium" if uncertain_score >= 25 else "low"),
            "limitations": uncertainty_notes,
        },
    }
