"""Language dispatch: analyse a repository with the right analyzer(s).

`analyse_repository` scans the tree once, then routes:

  * Python files      -> python_analyzer (stdlib `ast`)
  * JS/TS/JSX/TSX     -> js_analyzer   (tree-sitter; Express/Fastify routes,
                          imports, calls, fetch/axios HTTP clients)
  * mixed repositories -> both, with components/edges merged into one graph

Both analyzers emit the same component/edge format, so downstream consumers
(graph engine, findings, API, frontend) need no changes.

Incremental analysis: per-file extraction models are cached (signed JSON) by
content hash under `<data_dir>/analyzer-cache/<project-key>/`; on re-analysis
unchanged files reuse their cached model (see `cache.py`).

Recursion headroom: the analyzers recurse over source structure (the Python
resolver walks one frame per link in a construction/attribute chain, and `ast`
accepts attribute chains of unbounded length — paren nesting is capped by the
parser, attribute chains are not). CPython's default limit of 1000 frames is
reachable with real machine-generated code. Importing this module must not
change the process-wide limit, so `analyse_repository` runs its work in a
dedicated worker thread that raises the limit and carries an enlarged stack
(64 MB), restoring the previous limit when it finishes. Concurrency note: the
limit is process-wide in CPython, so a second analysis restoring it while
another is still deep could break the first; analyses are therefore
serialised with a lock — they are CPU-heavy and single-user anyway.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from app.analyzer.cache import default_cache_dir
from app.analyzer.ingest import scan_tree
from app.analyzer.js_analyzer import JS_LANGS, analyse_js_project
from app.analyzer.python_analyzer import analyse_project

log = logging.getLogger("devdna.analysis")

_ANALYSIS_RECURSION_LIMIT = 20000
_ANALYSIS_STACK_BYTES = 64 * 1024 * 1024
# One analysis at a time: the recursion limit is process-wide, so restoring it
# at worker exit must not race another running analysis.
_analysis_lock = threading.Lock()

JS_SUFFIXES = tuple(JS_LANGS)


def _is_js_file(rel: str) -> bool:
    name = rel.split("/")[-1]
    return name.endswith(JS_SUFFIXES) and not name.endswith(".d.ts") and ".min." not in name


def _merge_results(py: dict, js: dict) -> dict:
    """Merge a Python and a JS/TS analysis into one result.

    Component ids that collide across languages (both analyse a `route:GET /ping`)
    are namespaced with a `js:` prefix on the JS side, and the JS edges that
    referenced the old ids are remapped. Edges are then deduplicated by
    (type, source, target) endpoint, not just by edge id. Coverage counts come
    from the merged component/edge lists, never from per-language sums.
    """
    seen_ids = {c["id"] for c in py["components"]}
    js_components = []
    id_map: dict[str, str] = {}
    for c in js["components"]:
        cid = c["id"]
        if cid in seen_ids:
            renamed = f"js:{cid}"
            while renamed in seen_ids:
                renamed = f"js:{renamed}"
            id_map[cid] = renamed
            c = {**c, "id": renamed}
            seen_ids.add(renamed)
        js_components.append(c)

    def remap(component_id: str) -> str:
        return id_map.get(component_id, component_id)

    py_endpoints = {(e["source"], e["target"], e["type"]) for e in py["edges"]}
    seen_edges = {e["id"] for e in py["edges"]}
    js_edges = []
    for e in js["edges"]:
        source, target = remap(e["source"]), remap(e["target"])
        if (source, target, e["type"]) in py_endpoints:
            continue
        eid = f"{e['type']}|{source}|{target}"
        if eid in seen_edges:
            continue
        js_edges.append({**e, "id": eid, "source": source, "target": target})
        seen_edges.add(eid)

    languages = dict(py["scan"]["languages"])
    for lang, count in js["scan"]["languages"].items():
        languages[lang] = languages.get(lang, 0) + count

    def merged_incremental(a: dict, b: dict) -> dict:
        out = dict(a)
        for k in ("files_reused", "files_parsed", "cache_writes_failed"):
            out[k] = out.get(k, 0) + b.get(k, 0)
        out["enabled"] = a.get("enabled", False) or b.get("enabled", False)
        return out

    incremental = merged_incremental(py["scan"].get("incremental", {}), js["scan"].get("incremental", {}))
    deps = {(d["name"], d["manifest"]): d for d in py["dependencies"]}
    for d in js["dependencies"]:
        deps.setdefault((d["name"], d["manifest"]), d)
    coverage = {**py["coverage"], **js["coverage"]}
    coverage["parse_errors"] = py["coverage"]["parse_errors"] + js["coverage"]["parse_errors"]
    # coverage is counted from the merged lists: summing per-language counts
    # double-counts routes/edges whose ids collided and were namespaced
    components = py["components"] + js_components
    edges = py["edges"] + js_edges
    route_ids = {c["id"] for c in components if c["type"] == "route"}
    tested = {e["target"] for e in edges if e["type"] == "exercises"}
    coverage["routes"] = len(route_ids)
    coverage["routes_with_tests"] = len(route_ids & tested)
    coverage["tests"] = sum(1 for c in components if c["type"] == "test")
    return {
        "components": components,
        "edges": edges,
        "scan": {
            **py["scan"],
            "languages": languages,
            "analysed_languages": py["scan"]["analysed_languages"] + js["scan"]["analysed_languages"],
            "incremental": incremental,
        },
        "frameworks": sorted(set(py["frameworks"]) | set(js["frameworks"])),
        "third_party_imports": sorted(set(py["third_party_imports"]) | set(js["third_party_imports"])),
        "dependencies": list(deps.values()),
        "coverage": coverage,
        "secret_hits": py["secret_hits"] + js["secret_hits"],
        "signals": {
            "http_calls": py["signals"]["http_calls"] + js["signals"]["http_calls"],
            "broad_excepts": py["signals"]["broad_excepts"] + js["signals"]["broad_excepts"],
            "env_required": py["signals"]["env_required"] + js["signals"]["env_required"],
        },
    }


def _run_with_recursion_headroom(fn):
    """Run fn() in a dedicated thread with a raised recursion limit and an
    enlarged stack, restoring the previous limit on exit.

    Chosen over finishing the iterative rewrite of the remaining recursive
    walkers as the smaller safe change: the resolver's recursion depth is data
    dependent, the workers are short-lived, and CPython 3.11+ allocates
    pure-Python frames on the heap, so the raised limit is safe. Analyses are
    serialised so the limit restore cannot race a still-running analysis.
    """
    result: dict = {}

    def worker():
        previous_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(_ANALYSIS_RECURSION_LIMIT)
        try:
            result["value"] = fn()
        except BaseException as exc:  # re-raised on the caller's thread
            result["error"] = exc
        finally:
            sys.setrecursionlimit(previous_limit)

    try:
        previous_stack = threading.stack_size(_ANALYSIS_STACK_BYTES)
    except (ValueError, OSError):  # platform refuses the size; default stack
        previous_stack = None
    try:
        thread = threading.Thread(target=worker, name="devdna-analysis", daemon=True)
        thread.start()
    finally:
        if previous_stack is not None:
            threading.stack_size(previous_stack)
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


def analyse_repository(root: Path, progress=None, cache_dir: Path | None = None,
                       use_cache: bool = True) -> dict:
    """Analyse a repository with the appropriate analyzer(s) and log time/file counts."""
    started = time.perf_counter()
    root = Path(root)
    if cache_dir is None:
        cache_dir = default_cache_dir(root)

    def analyse():
        tree = scan_tree(root)
        py_files = [f for f in tree["files"] if f.endswith(".py")]
        js_files = [f for f in tree["files"] if _is_js_file(f)]

        # no dispatcher-level progress steps: the analyzers emit their own stage
        # list starting at "Scanning files", which the UI and tests rely on
        if not js_files:  # python-only (also covers empty repositories)
            result = analyse_project(root, progress, tree=tree, cache_dir=cache_dir, use_cache=use_cache)
        elif not py_files:  # JS/TS-only
            result = analyse_js_project(root, progress, tree=tree, cache_dir=cache_dir, use_cache=use_cache)
        else:  # mixed
            py_result = analyse_project(root, progress, tree=tree,
                                        cache_dir=cache_dir, use_cache=use_cache)
            js_result = analyse_js_project(root, progress, tree=tree,
                                           cache_dir=cache_dir, use_cache=use_cache)
            result = _merge_results(py_result, js_result)

        elapsed = time.perf_counter() - started
        analysed = result["scan"]["file_count"]
        reused = result["scan"].get("incremental", {}).get("files_reused", 0)
        log.info("Analysed %d files (%d Python, %d JS/TS) in %.2fs (%d from cache)",
                 analysed, len(py_files), len(js_files), elapsed, reused)
        return result

    with _analysis_lock:
        return _run_with_recursion_headroom(analyse)
