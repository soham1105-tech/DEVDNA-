"""DevDNA Change Intelligence - Intelligent Test Prioritisation Service.

Prioritises tests into 5 explicit tiers:
- Tier 1: Tests directly exercising modified components
- Tier 2: Tests exercising direct dependents
- Tier 3: Tests exercising transitively affected components
- Tier 4: Critical API integration tests covering impacted routes
- Tier 5: Relevant regression tests in affected modules
"""
from __future__ import annotations

import logging
from typing import Any

import networkx as nx

log = logging.getLogger("devdna.test_prioritization")

TIER_CONFIG = {
    1: {"name": "Direct Component Test", "base_score": 95},
    2: {"name": "Direct Dependent Test", "base_score": 80},
    3: {"name": "Transitive Propagation Test", "base_score": 65},
    4: {"name": "Critical API Integration Test", "base_score": 50},
    5: {"name": "Regression Test", "base_score": 30},
}


def prioritize_tests(
    g: nx.DiGraph,
    graph_diff: dict[str, Any],
) -> dict[str, Any]:
    """Select and rank tests most relevant to the code change."""
    directly_affected_ids = {c["id"] for c in graph_diff.get("directly_affected", [])}
    transitively_affected_ids = {c["id"] for c in graph_diff.get("transitively_affected", [])}
    impacted_routes_ids = {r["id"] for r in graph_diff.get("impacted_routes", [])}

    # Direct dependents of directly affected components
    direct_dependents_ids = set()
    for sid in directly_affected_ids:
        # Predecessors in g with propagating edges call or import sid
        if sid in g:
            for pred in g.predecessors(sid):
                if pred not in directly_affected_ids and g.nodes.get(pred, {}).get("type") != "test":
                    direct_dependents_ids.add(pred)

    prioritized_list: list[dict[str, Any]] = []
    seen_tests: set[str] = set()

    # Find all test nodes in the graph
    test_nodes = [(n, d) for n, d in g.nodes(data=True) if d.get("type") == "test"]

    for tid, data in test_nodes:
        # Find all components this test exercises
        exercised = set()
        for _, succ, edata in g.out_edges(tid, data=True):
            if edata.get("type") == "exercises":
                exercised.add(succ)

        is_mocked = data.get("data", {}).get("is_mocked", False)
        nodeid = data.get("data", {}).get("nodeid") or tid

        # Check Tier 1: Directly exercises modified component
        direct_matches = exercised & directly_affected_ids
        if direct_matches:
            target_name = g.nodes[next(iter(direct_matches))].get("name", "modified component")
            score = TIER_CONFIG[1]["base_score"] - (15 if is_mocked else 0)
            prioritized_list.append({
                "test_id": tid,
                "name": data.get("name", tid),
                "nodeid": nodeid,
                "file": data.get("file"),
                "line": data.get("line"),
                "tier": 1,
                "tier_name": TIER_CONFIG[1]["name"],
                "priority_score": score,
                "selection_reason": f"Directly exercises modified component '{target_name}'.",
                "related_components": list(direct_matches),
                "coverage_confidence": "low" if is_mocked else "high",
                "is_mocked": is_mocked,
            })
            seen_tests.add(tid)
            continue

        # Check Tier 2: Exercises direct dependent of modified component
        dep_matches = exercised & direct_dependents_ids
        if dep_matches:
            target_name = g.nodes[next(iter(dep_matches))].get("name", "dependent component")
            score = TIER_CONFIG[2]["base_score"] - (10 if is_mocked else 0)
            prioritized_list.append({
                "test_id": tid,
                "name": data.get("name", tid),
                "nodeid": nodeid,
                "file": data.get("file"),
                "line": data.get("line"),
                "tier": 2,
                "tier_name": TIER_CONFIG[2]["name"],
                "priority_score": score,
                "selection_reason": f"Exercises direct dependent '{target_name}' which calls modified code.",
                "related_components": list(dep_matches),
                "coverage_confidence": "low" if is_mocked else "medium",
                "is_mocked": is_mocked,
            })
            seen_tests.add(tid)
            continue

        # Check Tier 4: Critical API integration test
        route_matches = exercised & impacted_routes_ids
        if route_matches:
            route_name = g.nodes[next(iter(route_matches))].get("name", "API route")
            score = TIER_CONFIG[4]["base_score"] - (10 if is_mocked else 0)
            prioritized_list.append({
                "test_id": tid,
                "name": data.get("name", tid),
                "nodeid": nodeid,
                "file": data.get("file"),
                "line": data.get("line"),
                "tier": 4,
                "tier_name": TIER_CONFIG[4]["name"],
                "priority_score": score,
                "selection_reason": f"Integrates client-facing route '{route_name}' impacted by change propagation.",
                "related_components": list(route_matches),
                "coverage_confidence": "medium",
                "is_mocked": is_mocked,
            })
            seen_tests.add(tid)
            continue

        # Check Tier 3: Exercises transitively affected components
        trans_matches = exercised & transitively_affected_ids
        if trans_matches:
            target_name = g.nodes[next(iter(trans_matches))].get("name", "transitive component")
            score = TIER_CONFIG[3]["base_score"] - (10 if is_mocked else 0)
            prioritized_list.append({
                "test_id": tid,
                "name": data.get("name", tid),
                "nodeid": nodeid,
                "file": data.get("file"),
                "line": data.get("line"),
                "tier": 3,
                "tier_name": TIER_CONFIG[3]["name"],
                "priority_score": score,
                "selection_reason": f"Exercises transitively affected component '{target_name}'.",
                "related_components": list(trans_matches),
                "coverage_confidence": "low" if is_mocked else "medium",
                "is_mocked": is_mocked,
            })
            seen_tests.add(tid)
            continue

    # Also check associated tests from BFS in graph_diff that didn't have direct exercise edges
    for assoc in graph_diff.get("associated_tests", []):
        atid = assoc["id"]
        if atid not in seen_tests:
            is_mocked = assoc.get("is_mocked", False)
            nodeid = assoc.get("nodeid") or atid
            dist = assoc.get("distance", 3)
            tier = 3 if dist <= 2 else 5
            score = TIER_CONFIG[tier]["base_score"] - (dist * 2) - (10 if is_mocked else 0)
            prioritized_list.append({
                "test_id": atid,
                "name": assoc.get("name", atid),
                "nodeid": nodeid,
                "file": assoc.get("file"),
                "line": assoc.get("line"),
                "tier": tier,
                "tier_name": TIER_CONFIG[tier]["name"],
                "priority_score": max(10, score),
                "selection_reason": f"Reached via multi-hop reverse dependency path (distance {dist}).",
                "related_components": assoc.get("path", [])[-3:],
                "coverage_confidence": "low" if is_mocked else "medium",
                "is_mocked": is_mocked,
            })
            seen_tests.add(atid)

    # Sort tests by priority_score desc, tier asc, name asc
    prioritized_list.sort(key=lambda t: (-t["priority_score"], t["tier"], t["name"]))

    tier_counts = {i: sum(1 for t in prioritized_list if t["tier"] == i) for i in range(1, 6)}
    estimated_confidence = "high" if tier_counts[1] > 0 else ("medium" if tier_counts[2] > 0 or tier_counts[4] > 0 else "low")

    return {
        "summary": {
            "total_prioritized_tests": len(prioritized_list),
            "tier_1_direct": tier_counts[1],
            "tier_2_dependent": tier_counts[2],
            "tier_3_transitive": tier_counts[3],
            "tier_4_integration": tier_counts[4],
            "tier_5_regression": tier_counts[5],
            "estimated_coverage_confidence": estimated_confidence,
        },
        "tests": prioritized_list,
    }
