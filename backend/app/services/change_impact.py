"""Change-impact: predict what a set of changed files affects.

Given changed file paths (e.g. from a git diff), map them onto the analysed
graph and run a multi-source reverse BFS over propagating edges to report:

* dependents that *may* be affected (static reachability is not proof of
  breakage — every prediction here is 'possible', never 'definite');
* tests that exercise the changed code (predicted 'may_break', never 'fail');
* changed files with no covering test (a test-gap signal, reported per file);
* files that could not be mapped onto the graph at all.

This is the deterministic core the PR-intelligence layer builds on. Honesty
rules: predictions are labelled as such, every result carries its method and
limitations, and nothing here is proof that behaviour changed.
"""
from __future__ import annotations

from app.graph.engine import PROPAGATING, _bfs, _path

MAX_FILES = 200


def normalise_path(p: str) -> str:
    """Normalise a changed-file path for matching against component files:
    strip diff prefixes (a/, b/), leading ./, and convert backslashes."""
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p.lstrip("/")


def changed_files_impact(g, files: list[str]) -> dict:
    """Compute the reverse reachability impact of a set of changed files.

    `g` is the built graph (see app.graph.engine). Deterministic: all outputs
    are sorted; ties break by node id.
    """
    norm = {f: normalise_path(f) for f in files}
    by_file: dict[str, list[str]] = {}
    for n, data in g.nodes(data=True):
        f = data.get("file")
        if f:
            by_file.setdefault(f.replace("\\", "/"), []).append(n)

    mapped: dict[str, list[str]] = {}
    unmapped: list[dict] = []
    for raw in files:
        nodes = sorted(by_file.get(norm[raw], []))
        if nodes:
            mapped[raw] = nodes
        else:
            unmapped.append({"file": raw,
                             "reason": "no analysed component references this path "
                                       "(not analysed, non-code, or outside the analysed root)"})

    seeds = sorted({n for nodes in mapped.values() for n in nodes})
    parent, dist = _bfs(g, seeds, None)

    affected = []
    for n, data in g.nodes(data=True):
        if n in parent and n not in seeds:
            path = _path(parent, n)
            affected.append({
                "id": n, "type": data["type"], "name": data["name"],
                "file": data.get("file"), "line": data.get("line"),
                "certainty": "possible",
                "distance": dist[n], "path": path,
            })
    affected.sort(key=lambda a: (a["distance"], a["id"]))

    tests_may_break = []
    tests_unaffected_count = 0
    for n, data in g.nodes(data=True):
        if data["type"] != "test":
            continue
        if n in parent and n not in seeds:
            tests_may_break.append({
                "id": n, "name": data["name"], "file": data.get("file"), "line": data.get("line"),
                "nodeid": data["data"].get("nodeid"),
                "prediction": "may_break",
                "distance": dist[n],
            })
        else:
            tests_unaffected_count += 1
    tests_may_break.sort(key=lambda t: (t["distance"], t["id"]))

    # test-gap signal: changed files no test can reach
    reaching_tests: dict[str, int] = {raw: 0 for raw in mapped}
    for t in tests_may_break:
        # walk the test's path back to whichever seed it touches
        path = _path(parent, t["id"])
        touched = {s for s in seeds if s in path}
        for s in touched:
            for raw, nodes in mapped.items():
                if s in nodes:
                    reaching_tests[raw] += 1
    uncovered_files = sorted(raw for raw, cnt in reaching_tests.items() if cnt == 0)

    prop_edge_count = sum(1 for _, _, d in g.edges(data=True) if d["type"] in PROPAGATING)
    return {
        "changed_files": sorted(mapped),
        "unmapped_files": unmapped,
        "seeds": seeds,
        "affected": affected,
        "tests_may_break": tests_may_break,
        "uncovered_files": uncovered_files,
        "summary": {
            "files_mapped": len(mapped),
            "files_unmapped": len(unmapped),
            "affected_total": len(affected),
            "tests_may_break": len(tests_may_break),
            "tests_unaffected": tests_unaffected_count,
            "files_without_test_coverage": len(uncovered_files),
            "graph_edges_propagating": prop_edge_count,
        },
        "method": (
            "Multi-source reverse breadth-first search over propagating edges "
            "(calls, handled_by, served_by, runs_on_startup, uses_database, calls_http, reads_env, "
            "exercises) from every graph component in the changed files."
        ),
        "limitations": [
            "Static reachability is not proof of breakage: every dependent is labelled 'possible' "
            "and every test 'may_break'. Only a controlled experiment can turn a prediction into an observation.",
            "Files with no analysed component are reported as unmapped, not ignored.",
            "Dynamic dispatch, reflection and monkeypatching are not modelled; import-time code is not part of the graph.",
            "A file with no covering test is a test-gap signal, not proof the code is untested at runtime.",
        ],
    }
