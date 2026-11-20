"""DevDNA Change Intelligence - Before-and-After Graph Dependency Analysis.

Compares the dependency graph of a base branch/revision against a modified branch/revision:
- Newly introduced dependencies (new edges)
- Removed dependencies (deleted edges)
- Modified dependency relationships
- Directly affected components (modified in the change)
- Transitively affected components (downstream dependents)
- Potentially affected components (conditional/mocked propagation)
- Uncertain components (unresolved or dynamic reachability)
- Impacted API routes and associated tests
"""
from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from app.graph.engine import PROPAGATING, _bfs, _path

log = logging.getLogger("devdna.graph_diff")

CRITICAL_TYPES = {"route", "database", "auth", "payment", "security"}
CRITICAL_SUBSTRINGS = {"db", "database", "session", "auth", "token", "payment", "checkout", "order"}


def _is_critical_component(node_id: str, data: dict[str, Any], in_degree: int) -> tuple[bool, str]:
    """Determine if a component is critical based on type, high fan-in, or domain semantics."""
    ctype = data.get("type", "")
    cname = (data.get("name") or "").lower()
    cfile = (data.get("file") or "").lower()

    if ctype == "route":
        return True, "API route exposed to clients"
    if in_degree >= 4:
        return True, f"High fan-in hub ({in_degree} dependent components)"
    if any(term in cname or term in cfile for term in CRITICAL_SUBSTRINGS):
        return True, f"Core subsystem component ({ctype})"
    return False, ""


def diff_dependency_graphs(
    base_graph: nx.DiGraph,
    mod_graph: nx.DiGraph,
    changed_components: list[dict[str, Any]],
    changed_files: list[str],
) -> dict[str, Any]:
    """Compare base and modified dependency graphs and compute change propagation."""
    # 1. Edge diffing
    base_edges = {(u, v, d.get("type", "unknown")) for u, v, d in base_graph.edges(data=True)}
    mod_edges = {(u, v, d.get("type", "unknown")) for u, v, d in mod_graph.edges(data=True)}

    new_edge_tuples = mod_edges - base_edges
    removed_edge_tuples = base_edges - mod_edges

    new_dependencies = []
    for u, v, etype in sorted(new_edge_tuples):
        u_data = mod_graph.nodes.get(u, {})
        v_data = mod_graph.nodes.get(v, {})
        new_dependencies.append({
            "source": u,
            "source_name": u_data.get("name", u),
            "target": v,
            "target_name": v_data.get("name", v),
            "type": etype,
            "status": "introduced",
        })

    removed_dependencies = []
    for u, v, etype in sorted(removed_edge_tuples):
        u_data = base_graph.nodes.get(u, {})
        v_data = base_graph.nodes.get(v, {})
        removed_dependencies.append({
            "source": u,
            "source_name": u_data.get("name", u),
            "target": v,
            "target_name": v_data.get("name", v),
            "type": etype,
            "status": "removed",
        })

    # 2. Map changed seeds into modified graph
    # Seeds can be explicitly changed components, or all components belonging to changed files
    norm_changed_files = {f.replace("\\", "/").lstrip("./").lstrip("/") for f in changed_files}
    direct_seed_ids = set()

    for comp in changed_components:
        cid = comp.get("id")
        if cid and cid in mod_graph:
            direct_seed_ids.add(cid)

    # If function-level seeds were not matched, fall back to components in the changed files
    for n, data in mod_graph.nodes(data=True):
        cfile = (data.get("file") or "").replace("\\", "/").lstrip("./").lstrip("/")
        if cfile in norm_changed_files:
            direct_seed_ids.add(n)

    seeds = sorted(direct_seed_ids)

    # 3. Propagate impact along reverse propagating edges in mod_graph
    # Ensure graph is MultiDiGraph for _bfs
    if not isinstance(mod_graph, nx.MultiDiGraph):
        mg = nx.MultiDiGraph()
        mg.add_nodes_from(mod_graph.nodes(data=True))
        for u, v, d in mod_graph.edges(data=True):
            mg.add_edge(u, v, **d)
        mod_graph = mg

    parent, dist = _bfs(mod_graph, seeds, None)

    directly_affected = []
    transitively_affected = []
    potentially_affected = []
    uncertain_components = []
    impacted_routes = []
    associated_tests = []
    critical_affected = []

    # Map directly affected
    for sid in seeds:
        data = mod_graph.nodes[sid]
        in_deg = mod_graph.in_degree(sid) if sid in mod_graph else 0
        is_crit, crit_reason = _is_critical_component(sid, data, in_deg)
        item = {
            "id": sid,
            "name": data.get("name", sid),
            "type": data.get("type", "unknown"),
            "file": data.get("file"),
            "line": data.get("line"),
            "category": "directly_affected",
            "distance": 0,
            "path": [sid],
            "is_critical": is_crit,
            "critical_reason": crit_reason if is_crit else None,
        }
        directly_affected.append(item)
        if is_crit:
            critical_affected.append(item)

    # Check downstream reached nodes
    for n, data in mod_graph.nodes(data=True):
        if n in seeds or n not in parent:
            continue

        distance = dist[n]
        path = _path(parent, n)
        ntype = data.get("type", "unknown")
        in_deg = mod_graph.in_degree(n)
        is_crit, crit_reason = _is_critical_component(n, data, in_deg)

        # Check for potential mocking or conditional handling
        is_mocked = data.get("data", {}).get("is_mocked", False)
        certainty = "potentially_affected" if is_mocked else "transitively_affected"

        node_info = {
            "id": n,
            "name": data.get("name", n),
            "type": ntype,
            "file": data.get("file"),
            "line": data.get("line"),
            "category": certainty,
            "distance": distance,
            "path": path,
            "is_critical": is_crit,
            "critical_reason": crit_reason if is_crit else None,
        }

        if ntype == "route":
            impacted_routes.append({
                "id": n,
                "route": data.get("name", n),
                "verb": data.get("data", {}).get("verb", "UNKNOWN"),
                "file": data.get("file"),
                "line": data.get("line"),
                "distance": distance,
                "path": path,
            })
            critical_affected.append(node_info)
        elif ntype == "test":
            associated_tests.append({
                "id": n,
                "name": data.get("name", n),
                "file": data.get("file"),
                "line": data.get("line"),
                "nodeid": data.get("data", {}).get("nodeid"),
                "distance": distance,
                "path": path,
                "is_mocked": is_mocked,
            })
        else:
            if certainty == "transitively_affected":
                transitively_affected.append(node_info)
            else:
                potentially_affected.append(node_info)

            if is_crit:
                critical_affected.append(node_info)

    # Check for newly exposed downstream components (gained paths due to new edges)
    newly_exposed = []
    for dep in new_dependencies:
        src = dep["source"]
        if src in parent and src not in seeds:
            newly_exposed.append({
                "component_id": src,
                "introduced_via": f"{dep['source_name']} -> {dep['target_name']}",
                "edge_type": dep["type"],
            })

    # Sort all lists deterministically
    directly_affected.sort(key=lambda x: x["id"])
    transitively_affected.sort(key=lambda x: (x["distance"], x["id"]))
    potentially_affected.sort(key=lambda x: (x["distance"], x["id"]))
    impacted_routes.sort(key=lambda x: (x["distance"], x["id"]))
    associated_tests.sort(key=lambda x: (x["distance"], x["id"]))
    critical_affected.sort(key=lambda x: (x["distance"], x["id"]))

    total_components = mod_graph.number_of_nodes()
    total_affected = len(directly_affected) + len(transitively_affected) + len(potentially_affected)
    blast_radius_ratio = round(total_affected / total_components, 4) if total_components else 0.0

    return {
        "summary": {
            "total_nodes_base": base_graph.number_of_nodes(),
            "total_nodes_modified": mod_graph.number_of_nodes(),
            "total_edges_base": base_graph.number_of_edges(),
            "total_edges_modified": mod_graph.number_of_edges(),
            "new_dependencies_count": len(new_dependencies),
            "removed_dependencies_count": len(removed_dependencies),
            "directly_affected_count": len(directly_affected),
            "transitively_affected_count": len(transitively_affected),
            "potentially_affected_count": len(potentially_affected),
            "impacted_routes_count": len(impacted_routes),
            "associated_tests_count": len(associated_tests),
            "critical_components_count": len(critical_affected),
            "blast_radius_ratio": blast_radius_ratio,
        },
        "new_dependencies": new_dependencies,
        "removed_dependencies": removed_dependencies,
        "directly_affected": directly_affected,
        "transitively_affected": transitively_affected,
        "potentially_affected": potentially_affected,
        "uncertain_components": uncertain_components,
        "critical_components_affected": critical_affected,
        "impacted_routes": impacted_routes,
        "associated_tests": associated_tests,
        "newly_exposed_downstream": newly_exposed,
    }
