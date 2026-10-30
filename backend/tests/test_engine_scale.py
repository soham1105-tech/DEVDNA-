"""Scale behaviour of the graph engine: approximate betweenness, caps, and a
2,000-file synthetic repository end-to-end (analysis + metrics + incremental).
"""
import time
from pathlib import Path

import networkx as nx
import pytest

import app.graph.engine as eng
from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph, compute_metrics


def _component(cid, ctype="function", file=None, line=1):
    return {"id": cid, "type": ctype, "name": cid, "file": file, "line": line, "data": {}}


def _call(source, target):
    return {"id": f"calls|{source}|{target}", "source": source, "target": target, "type": "calls",
            "classification": "confirmed", "evidence": [], "call_sites": []}


def test_metrics_are_identical_to_per_node_dfs_on_random_graphs():
    import random

    random.seed(7)
    for _ in range(5):
        n = random.randint(20, 80)
        comps = [_component(f"n{i}") for i in range(n)]
        edges = [_call(f"n{i}", f"n{j}") for i in range(n) for j in range(n)
                 if i != j and random.random() < 0.06]
        g = build_graph(comps, edges)
        metrics = compute_metrics(g)
        d = eng._prop_digraph(g)
        for node in d.nodes:
            assert metrics[node]["dependencies"] == len(nx.descendants(d, node))
            assert metrics[node]["dependents"] == len(
                [a for a in nx.ancestors(d, node) if g.nodes[a]["type"] != "test"])


def test_betweenness_switches_to_sampled_above_threshold_and_stays_deterministic():
    # two dense halves joined by a single bridge: a classic high-betweenness graph
    half_a = [f"a{i}" for i in range(300)]
    half_b = [f"b{i}" for i in range(300)]
    comps = [_component(n) for n in half_a + half_b]
    edges = [_call(half_a[i], half_a[i + 1]) for i in range(299)]
    edges += [_call(half_b[i], half_b[i + 1]) for i in range(299)]
    edges.append(_call(half_a[-1], half_b[0]))  # bridge

    g1 = build_graph(comps, edges)
    m1 = compute_metrics(g1)
    assert g1.graph["betweenness_mode"] == "approximate"
    g2 = build_graph(comps, edges)
    m2 = compute_metrics(g2)
    assert m1 == m2
    # the bridge node should rank among the most central
    bridge = m1[half_a[-1]]["betweenness"]
    assert bridge > m1[half_a[5]]["betweenness"]


def test_node_cap_drops_low_priority_types_with_warning():
    comps = ([_component(f"route:{i}", "route") for i in range(5)]
             + [_component(f"func:{i}", "function") for i in range(20)]
             + [_component(f"test:{i}", "test") for i in range(15)])
    original_cap = eng.NODE_CAP
    eng.NODE_CAP = 25
    try:
        g = build_graph(comps, [])
        assert g.number_of_nodes() == 25
        warnings = g.graph["warnings"]
        assert warnings and "Node cap (25) exceeded" in warnings[0]
        # tests are dropped first; routes are all retained
        assert sum(1 for n in g.nodes if g.nodes[n]["type"] == "test") == 0
        assert sum(1 for n in g.nodes if g.nodes[n]["type"] == "route") == 5
    finally:
        eng.NODE_CAP = original_cap


def test_edge_cap_warns_and_stops_adding():
    comps = [_component(f"n{i}") for i in range(12)]
    edges = [_call(f"n{i}", f"n{i + 1}") for i in range(11)]
    original_cap = eng.EDGE_CAP
    eng.EDGE_CAP = 4
    try:
        g = build_graph(comps, edges)
        assert g.number_of_edges() == 4
        assert any("Edge cap (4) exceeded" in w for w in g.graph["warnings"])
    finally:
        eng.EDGE_CAP = original_cap


def _generate_synthetic_repo(root: Path, files: int = 2000) -> list[Path]:
    src = root / "src"
    src.mkdir(parents=True)
    paths = []
    for i in range(files):
        if i + 1 < files:
            body = (f"import {{ work{i + 1} }} from './mod{i + 1}.js';\n"
                    f"export function work{i}(x) {{ return work{i + 1}(x + {i}); }}\n")
        else:
            body = f"export function work{i}(x) {{ return x; }}\n"
        if i % 200 == 0:
            body += f"export async function handle{i}(req, res) {{ return work{i}(1); }}\n"
        path = src / f"mod{i}.js"
        path.write_text(body, encoding="utf-8")
        paths.append(path)
    (src / "app.js").write_text(
        "import express from 'express';\n"
        "import { handle0 } from './mod0.js';\n"
        "const app = express();\n"
        "app.get('/work', handle0);\n"
        "export default app;\n",
        encoding="utf-8",
    )
    paths.append(src / "app.js")
    return paths


@pytest.fixture(scope="module")
def synthetic_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic")
    _generate_synthetic_repo(root, files=2000)
    return root


def test_2000_file_repo_completes_with_approximate_metrics(synthetic_repo):
    started = time.perf_counter()
    result = analyse_repository(synthetic_repo)
    elapsed = time.perf_counter() - started
    assert elapsed < 120, f"analysis took {elapsed:.1f}s"
    assert result["scan"]["file_count"] == 2001
    assert result["coverage"]["js_files"] == 2001
    assert "route:GET /work" in {c["id"] for c in result["components"]}

    g = build_graph(result["components"], result["edges"])
    metrics = compute_metrics(g)
    assert g.graph["betweenness_mode"] == "approximate"
    assert g.graph["metrics_seconds"] < 60
    # the call chain is intact: the first function reaches the whole chain
    assert metrics["func:mod0.work0"]["dependencies"] >= 1900


def test_2000_file_repo_second_run_reparses_only_changed_file(synthetic_repo):
    first = analyse_repository(synthetic_repo)
    total = first["scan"]["incremental"]["files_parsed"] + first["scan"]["incremental"]["files_reused"]
    assert total == 2001

    target = synthetic_repo / "src" / "mod7.js"
    target.write_text("export function work7(x) { return x * 2; }\n", encoding="utf-8")
    second = analyse_repository(synthetic_repo)
    inc = second["scan"]["incremental"]
    assert inc["files_reused"] == total - 1
    assert inc["files_parsed"] == 1
