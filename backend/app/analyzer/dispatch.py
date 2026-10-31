"""Language dispatch: analyse a repository with the right analyzer(s).

`analyse_repository` scans the tree once, then routes:

  * Python files      -> python_analyzer (stdlib `ast`)
  * JS/TS/JSX/TSX     -> js_analyzer   (tree-sitter; Express/Fastify routes,
                          imports, calls, fetch/axios HTTP clients)
  * mixed repositories -> both, with components/edges merged into one graph

Both analyzers emit the same component/edge format, so downstream consumers
(graph engine, findings, API, frontend) need no changes.

Incremental analysis: per-file parse/extract results are cached by content
hash under `<data_dir>/analyzer-cache/<project-key>/`; on re-analysis only
changed files are re-parsed (see `cache.py`).
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from app.analyzer.cache import default_cache_dir
from app.analyzer.ingest import scan_tree
from app.analyzer.js_analyzer import JS_LANGS, analyse_js_project
from app.analyzer.python_analyzer import analyse_project

log = logging.getLogger("devdna.analysis")

# The analyzers recurse over source structure: the Python resolver walks one
# frame per link in a construction/attribute chain, and `ast` accepts attribute
# chains of unbounded length (paren nesting is capped by the parser, attribute
# chains are not). CPython's default limit of 1000 frames is reachable with
# real machine-generated code, so give the process permanent headroom. Safe in
# CPython 3.11+: pure-Python frames are heap-allocated, not C-stack frames.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

JS_SUFFIXES = tuple(JS_LANGS)


def _is_js_file(rel: str) -> bool:
    name = rel.split("/")[-1]
    return name.endswith(JS_SUFFIXES) and not name.endswith(".d.ts") and ".min." not in name


def _merge_results(py: dict, js: dict) -> dict:
    """Merge a Python and a JS/TS analysis into one result (python keys win on scalar ties)."""
    seen_ids = {c["id"] for c in py["components"]}
    js_components = [c for c in js["components"] if c["id"] not in seen_ids]
    seen_edges = {e["id"] for e in py["edges"]}
    js_edges = [e for e in js["edges"] if e["id"] not in seen_edges]

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
    for k in ("routes", "routes_with_tests", "tests"):  # shared route/test id space -> sum
        coverage[k] = py["coverage"].get(k, 0) + js["coverage"].get(k, 0)
    return {
        "components": py["components"] + js_components,
        "edges": py["edges"] + js_edges,
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


def analyse_repository(root: Path, progress=None, cache_dir: Path | None = None,
                       use_cache: bool = True) -> dict:
    """Analyse a repository with the appropriate analyzer(s) and log time/file counts."""
    started = time.perf_counter()
    root = Path(root)
    if cache_dir is None:
        cache_dir = default_cache_dir(root)

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
