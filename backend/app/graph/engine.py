"""DevDNA deterministic graph engine.

Performs multi-relational graph propagation to estimate potential failure impact.

Edge direction is "source depends on target" (A --calls--> B means A needs B).
When B fails, every node that can reach B through *propagating* edges may be affected.

CRITICALITY SCORE (0-100, structural heuristic - NOT a failure probability):

    criticality = 100 * (0.5 * blast + 0.3 * betweenness_norm + 0.2 * route_share)

    blast            = non-test dependents of the node / (non-test nodes - 1)
    betweenness_norm = betweenness centrality / max betweenness in the graph
    route_share      = HTTP routes that (transitively) depend on the node / total routes

Scale behaviour (see compute_metrics / build_graph):
  * <= 500 nodes: exact betweenness.
  * >  500 nodes: sampled approximation (k=128 sources, fixed seed - still
    deterministic). The sample size is a knob, not a correctness limit: ranks
    stay stable, exact values may differ slightly from the exact algorithm.
  * node/edge caps guard against pathological repositories; excess nodes are
    dropped by type priority (tests first) with a warning recorded on the
    graph (g.graph["warnings"]) and in the run summary.
"""
from __future__ import annotations

import logging
import os
from collections import deque

import networkx as nx

log = logging.getLogger("devdna.engine")

PROPAGATING = {"calls", "handled_by", "served_by", "runs_on_startup", "uses_database", "calls_http", "reads_env", "exercises"}

# --- scale limits -----------------------------------------------------------
APPROX_BETWEENNESS_NODES = 500   # above this, switch to sampled betweenness
APPROX_BETWEENNESS_SAMPLES = 128  # number of pivots k for the approximation


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


NODE_CAP = _env_int("DEVDNA_NODE_CAP", 8000)
EDGE_CAP = _env_int("DEVDNA_EDGE_CAP", 40000)

# When the node cap bites, drop low-structural-value types first. Within a
# type, the analyzer's component order is kept (deterministic per run).
DROP_PRIORITY = {"test": 0, "module": 1, "class": 2, "function": 3}

SCENARIOS = {
    "db_unavailable": {
        "label": "Database unavailable",
        "target_types": ["database"],
        "exceptions": {"Error", "OperationalError", "DatabaseError", "InterfaceError", "SQLAlchemyError", "OSError", "ConnectionError"},
        "fault": "Every database connection attempt raises an OperationalError.",
    },
    "external_api_unavailable": {
        "label": "External API unavailable",
        "target_types": ["external_api"],
        "exceptions": {"HTTPError", "RequestError", "ConnectError", "TransportError", "TimeoutException", "RequestException",
                       "ConnectionError", "URLError", "OSError", "Timeout"},
        "fault": "Outbound HTTP transports raise a connection error before any request is sent.",
    },
    "missing_env_var": {
        "label": "Missing environment variable",
        "target_types": ["env_var"],
        "exceptions": {"KeyError", "LookupError", "<default>"},
        "fault": "The environment variable is removed from the process before every test.",
    },
    "worker_unavailable": {
        "label": "Background worker unavailable",
        "target_types": ["worker"],
        "exceptions": set(),
        "fault": "The worker's launch methods become no-ops, so the worker never starts.",
    },
}
GENERIC_EXCEPTIONS = {"*", "Exception", "BaseException"}


def build_graph(components: list[dict], edges: list[dict]) -> nx.MultiDiGraph:
    warnings: list[str] = []
    comps = list(components)
    if len(comps) > NODE_CAP:
        # keep the highest structural value: routes/apps/resources first, then
        # functions, classes, modules; tests last. Unknown types rank highest.
        order = sorted(range(len(comps)), key=lambda i: (-DROP_PRIORITY.get(comps[i]["type"], 4), i))
        keep = {comps[i]["id"] for i in order[:NODE_CAP]}
        comps = []
        dropped: dict[str, int] = {}
        for c in components:
            if c["id"] in keep:
                comps.append(c)
            else:
                dropped[c["type"]] = dropped.get(c["type"], 0) + 1
        detail = ", ".join(f"{v} {k}" for k, v in sorted(dropped.items()))
        warnings.append(f"Node cap ({NODE_CAP}) exceeded: {len(components) - NODE_CAP} components dropped "
                        f"({detail}); impact metrics cover the retained subgraph only.")
    g = nx.MultiDiGraph()
    for c in comps:
        g.add_node(c["id"], **c)
    skipped_edges = 0
    for e in edges:
        if e["source"] in g and e["target"] in g:
            if g.number_of_edges() >= EDGE_CAP:
                skipped_edges += 1
                continue
            g.add_edge(e["source"], e["target"], key=e["id"], **e)
    if skipped_edges:
        warnings.append(f"Edge cap ({EDGE_CAP}) exceeded: {skipped_edges} edges were not added to the graph.")
    if warnings:
        for w in warnings:
            log.warning("%s", w)
    g.graph["warnings"] = warnings
    return g


def _prop_digraph(g: nx.MultiDiGraph) -> nx.DiGraph:
    d = nx.DiGraph()
    d.add_nodes_from(g.nodes)
    for u, v, data in g.edges(data=True):
        if data["type"] in PROPAGATING:
            d.add_edge(u, v)
    return d


def _closure_bits(d: nx.DiGraph, reverse: bool):
    """Per-node ancestor (`reverse=True`) or descendant bitsets.

    Exact replacement for calling nx.ancestors/nx.descendants once per node
    (O(V*E) time and heavy allocator churn on large graphs). SCC condensation
    turns the digraph into a DAG; reachability over the DAG is accumulated as
    Python big-int bitsets, so the per-node result is a couple of C-speed
    popcounts. Results are identical to the per-node DFS.
    """
    graph = d.reverse(copy=False) if reverse else d
    nodes = sorted(d.nodes)
    bit = {n: i for i, n in enumerate(nodes)}
    cond = nx.condensation(graph)
    scc_members = [cond.nodes[i]["members"] for i in cond.nodes]
    node_scc = {n: i for i, members in enumerate(scc_members) for n in members}
    scc_mask = [0] * cond.number_of_nodes()
    for i, members in enumerate(scc_members):
        m = 0
        for n in members:
            m |= 1 << bit[n]
        scc_mask[i] = m
    reach: list[int] = [0] * cond.number_of_nodes()  # per-SCC reachability (excl. own members)
    for scc in reversed(list(nx.topological_sort(cond))):
        acc = 0
        for succ in cond.successors(scc):
            acc |= scc_mask[succ] | reach[succ]
        reach[scc] = acc
    out = {}
    for n in nodes:
        i = node_scc[n]
        out[n] = (reach[i] | scc_mask[i]) & ~(1 << bit[n])
    return out


def _betweenness(sub: nx.DiGraph) -> tuple[dict[str, float], bool]:
    """Betweenness centrality; sampled approximation above APPROX_BETWEENNESS_NODES.

    Returns (values, is_approximate). Sampling uses a fixed seed so results
    stay deterministic run to run.
    """
    n = sub.number_of_nodes()
    if n <= 2:
        return {v: 0.0 for v in sub}, False
    if n <= APPROX_BETWEENNESS_NODES:
        return nx.betweenness_centrality(sub), False
    k = min(APPROX_BETWEENNESS_SAMPLES, n)
    log.info("Graph has %d nodes; using sampled betweenness (k=%d) instead of exact", n, k)
    return nx.betweenness_centrality(sub, k=k, seed=0, normalized=True), True


def compute_metrics(g: nx.MultiDiGraph) -> dict[str, dict]:
    import time

    started = time.perf_counter()
    d = _prop_digraph(g)
    non_test = [n for n in d.nodes if g.nodes[n]["type"] != "test"]
    routes = [n for n in d.nodes if g.nodes[n]["type"] == "route"]
    sub = d.subgraph(non_test)
    betweenness, approx = _betweenness(sub)
    max_b = max(betweenness.values(), default=0) or 1.0
    # type masks for C-speed popcount filtering of ancestor sets; bit positions
    # must match the sorted order used by _closure_bits
    order = {n: i for i, n in enumerate(sorted(d.nodes))}
    test_mask = route_mask = 0
    for n in d.nodes:
        t = g.nodes[n]["type"]
        if t == "test":
            test_mask |= 1 << order[n]
        elif t == "route":
            route_mask |= 1 << order[n]
    anc_bits = _closure_bits(d, reverse=True)
    desc_bits = _closure_bits(d, reverse=False)
    metrics = {}
    for n in d.nodes:
        anc = anc_bits[n]
        dependents = (anc & ~test_mask).bit_count()
        tests = (anc & test_mask).bit_count()
        route_dep = (anc & route_mask).bit_count()
        blast = dependents / max(1, len(non_test) - 1)
        b = betweenness.get(n, 0.0)
        route_share = route_dep / len(routes) if routes else 0.0
        score = 100 * (0.5 * blast + 0.3 * (b / max_b) + 0.2 * route_share)
        metrics[n] = {
            "dependents": dependents,
            "dependencies": desc_bits[n].bit_count(),
            "tests_covering": tests,
            "routes_depending": route_dep,
            "in_degree": d.in_degree(n),
            "out_degree": d.out_degree(n),
            "betweenness": round(b, 4),
            "criticality": round(score, 1) if g.nodes[n]["type"] != "test" else 0.0,
        }
    g.graph["betweenness_mode"] = "approximate" if approx else "exact"
    g.graph["metrics_seconds"] = round(time.perf_counter() - started, 3)
    return metrics


def neighbourhood(g: nx.MultiDiGraph, node_id: str) -> dict:
    d = _prop_digraph(g)
    return {"upstream": sorted(nx.ancestors(d, node_id)), "downstream": sorted(nx.descendants(d, node_id))}


def _seeds(g: nx.MultiDiGraph, scenario: str, target: str) -> list[str]:
    seeds = [target]
    node = g.nodes[target]
    if scenario == "worker_unavailable":
        module, cls = node["data"].get("module"), node["data"].get("cls")
        launchers = set(node["data"].get("launcher_methods", []))
        for n, data in g.nodes(data=True):
            dd = data.get("data", {})
            if data["type"] == "function" and dd.get("module") == module and dd.get("cls") == cls:
                short = data["name"].split(".")[-1]
                if short in launchers or short.startswith("_"):
                    continue
                seeds.append(n)
    return seeds


def _relevant(names: list[str], relevant: set[str]) -> bool:
    return any(n.split(".")[-1] in relevant or n in relevant for n in names)


def _edge_guard(data: dict, relevant: set[str]) -> dict | None:
    """Return the protecting handler if *every* call site is inside a relevant, non-re-raising handler."""
    sites = data.get("call_sites") or []
    if not sites:
        return None
    found = None
    for site in sites:
        site_guard = None
        for handlers in site.get("tries", []):
            for h in handlers:
                if _relevant(h["names"], relevant) and not h["reraises"]:
                    site_guard = {"file": site.get("file"), "line": h.get("line"), "handles": h["names"]}
        if site_guard is None:
            return None
        found = found or site_guard
    return found


def _bfs(g: nx.MultiDiGraph, seeds: list[str], relevant: set[str] | None):
    """Reverse BFS. If `relevant` is given, edges whose call sites handle the failure are not followed."""
    parent: dict[str, tuple[str, str] | None] = {s: None for s in seeds}
    dist = {s: 0 for s in seeds}
    q = deque(seeds)
    while q:
        cur = q.popleft()
        for src, _, key, data in g.in_edges(cur, keys=True, data=True):
            if data["type"] not in PROPAGATING or src in parent:
                continue
            if relevant is not None and _edge_guard(data, relevant):
                continue
            parent[src] = (cur, key)
            dist[src] = dist[cur] + 1
            q.append(src)
    return parent, dist


def _path(parent, node) -> list[str]:
    out = [node]
    while parent.get(out[-1]):
        out.append(parent[out[-1]][0])
    return out


def compute_impact(g: nx.MultiDiGraph, scenario: str, target: str, custom_spec: dict | None = None) -> dict:
    if target not in g:
        raise ValueError(f"Component '{target}' is not in the analysed graph")
    if custom_spec:
        spec = dict(custom_spec)
        spec.setdefault("label", scenario.replace("_", " ").title())
        spec.setdefault("fault", f"Arbitrary dynamic failure injected for '{target}'.")
        spec.setdefault("exceptions", {"Exception", "BaseException", "RuntimeError", "Error"})
        spec.setdefault("target_types", [])
    elif scenario in SCENARIOS:
        spec = SCENARIOS[scenario]
    else:
        raise ValueError(f"Unsupported scenario '{scenario}'")
    ttype = g.nodes[target]["type"]
    if spec.get("target_types") and ttype not in spec["target_types"]:
        raise ValueError(f"Scenario '{spec['label']}' needs a {' or '.join(spec['target_types'])} component, got '{ttype}'")

    seeds = _seeds(g, scenario, target)
    possible_parent, possible_dist = _bfs(g, seeds, None)
    raised = set()
    for n in possible_parent:
        raised |= set(g.nodes[n].get("data", {}).get("raises", []))
    relevant = set(spec["exceptions"]) | GENERIC_EXCEPTIONS | raised
    definite_parent, definite_dist = _bfs(g, seeds, relevant)

    affected, tests = [], []
    for n, data in g.nodes(data=True):
        if n not in possible_parent or n in seeds:
            continue
        definite = n in definite_parent
        path = _path(definite_parent if definite else possible_parent, n)
        guard = None
        if not definite:
            for a, b in zip(path, path[1:]):
                for _, _, edata in g.edges(a, data=True):
                    if edata["target"] == b and edata["type"] in PROPAGATING:
                        guard = guard or _edge_guard(edata, relevant)
        item = {
            "id": n, "type": data["type"], "name": data["name"], "file": data.get("file"), "line": data.get("line"),
            "certainty": "definite" if definite else "possible",
            "distance": (definite_dist if definite else possible_dist)[n],
            "path": path, "handled_at": guard,
        }
        if data["type"] == "test":
            tests.append({**item, "nodeid": data["data"].get("nodeid"), "junit_key": data["data"].get("junit_key"),
                          "prediction": "fail" if definite else "may_fail"})
        else:
            affected.append(item)
    for n, data in g.nodes(data=True):
        if data["type"] == "test" and n not in possible_parent:
            tests.append({"id": n, "type": "test", "name": data["name"], "file": data.get("file"), "line": data.get("line"),
                          "nodeid": data["data"].get("nodeid"), "junit_key": data["data"].get("junit_key"),
                          "prediction": "pass", "certainty": "unaffected", "path": [], "distance": None, "handled_at": None})
    # Mock detection on covering tests
    covering_tests = [t for t in tests if t["id"] in possible_parent and t["id"] not in seeds]
    for t in covering_tests:
        node_data = g.nodes[t["id"]].get("data", {})
        mocks = node_data.get("mocks", [])
        mock_types = node_data.get("mock_types", [])
        is_mocked = False

        if ttype in mock_types:
            is_mocked = True
        elif scenario == "db_unavailable" and "database" in mock_types:
            is_mocked = True
        elif scenario == "external_api_unavailable" and "external_api" in mock_types:
            is_mocked = True

        if not is_mocked and len(t["path"]) > 1:
            nxt = t["path"][1]
            if g.has_edge(t["id"], nxt):
                for k, edata in g[t["id"]][nxt].items():
                    if edata.get("mocked"):
                        is_mocked = True
                        break

        if not is_mocked and mocks:
            for m in mocks:
                m_lower = m.lower()
                if any(p.lower() in m_lower or m_lower in p.lower() for p in t["path"]):
                    is_mocked = True
                    break

        if is_mocked:
            t["mocked"] = True
            if len(t["path"]) > 1:
                nxt = t["path"][1]
                if g.has_edge(t["id"], nxt):
                    for k, edata in g[t["id"]][nxt].items():
                        edata["mocked"] = True

    all_covering_mocked = len(covering_tests) > 0 and all(t.get("mocked") for t in covering_tests)
    overall_prediction = None
    if all_covering_mocked:
        overall_prediction = "uncertain — covering tests mock the dependency"
        for t in covering_tests:
            t["prediction"] = "uncertain"
            t["certainty"] = "uncertain"
            t["handled_at"] = {"mocked": True, "detail": "covering tests mock the dependency"}
    else:
        for t in covering_tests:
            if t.get("mocked"):
                t["prediction"] = "may_fail"
                t["certainty"] = "uncertain"
                t["handled_at"] = {"mocked": True, "detail": "test mocks the dependency"}

    pred_order = {"fail": 0, "may_fail": 1, "uncertain": 1, "pass": 2}
    affected.sort(key=lambda a: (a["certainty"] != "definite", a["distance"], a["id"]))
    tests.sort(key=lambda t: (pred_order.get(t["prediction"], 1), t["id"]))
    routes_total = sum(1 for _, d in g.nodes(data=True) if d["type"] == "route")
    affected_routes = [a for a in affected if a["type"] == "route"]
    tnode = g.nodes[target]
    custom_fault = {
        "kind": "dynamic_fault",
        "scenario": scenario,
        "target_id": target,
        "target_type": ttype,
        "target_name": tnode["name"],
        "target_data": tnode.get("data", {}),
        "exceptions": sorted(relevant),
        "fault_description": spec.get("fault", ""),
    } if (custom_spec or scenario not in SCENARIOS) else None

    return {
        "scenario": scenario,
        "scenario_label": spec["label"],
        "fault_description": spec["fault"],
        "custom_fault": custom_fault,
        "target": {"id": target, "type": ttype, "name": tnode["name"], "data": tnode.get("data", {})},
        "seeds": seeds,
        "relevant_exceptions": sorted(relevant),
        "affected": affected,
        "tests": tests,
        "prediction": overall_prediction,
        "summary": {
            "affected_total": len(affected),
            "definite": sum(1 for a in affected if a["certainty"] == "definite"),
            "possible": sum(1 for a in affected if a["certainty"] == "possible"),
            "routes_affected": len(affected_routes),
            "routes_definite": sum(1 for a in affected_routes if a["certainty"] == "definite"),
            "routes_total": routes_total,
            "tests_predicted_fail": sum(1 for t in tests if t["prediction"] == "fail"),
            "tests_predicted_may_fail": sum(1 for t in tests if t["prediction"] in ("may_fail", "uncertain")),
            "tests_predicted_pass": sum(1 for t in tests if t["prediction"] == "pass"),
            "prediction": overall_prediction,
        },
        "method": (
            "Reverse breadth-first search over propagating edges (calls, handled_by, served_by, runs_on_startup, "
            "uses_database, calls_http, reads_env, exercises). 'definite' = reachable without crossing a call site "
            "protected by an except-handler that catches a relevant exception and does not re-raise; 'possible' = "
            "reachable only through such handled call sites."
        ),
        "limitations": [
            "Static analysis only: dynamic dispatch, reflection, monkeypatching and runtime configuration are not modelled.",
            "An error being 'handled' does not mean the caller's behaviour is unchanged; handled paths are reported as uncertain.",
            "Module-level (import-time) code is not part of the propagation graph.",
            "Test-to-route mapping matches test-client URL literals against discovered route templates.",
        ],
    }
