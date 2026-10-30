"""Builds small, redacted, citeable evidence packages for Gemma prompts.

Only what is listed in a package is ever sent to the model, and the same package
is shown to the user in the consent dialog before sending.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.analyzer.redact import redact

MAX_EXCERPT_LINES = 30
MAX_ITEMS = 40


class Package:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, kind: str, title: str, content: str, source: str | None = None) -> str:
        if len(self.items) >= MAX_ITEMS:
            return ""
        ref = f"E{len(self.items) + 1}"
        content, _ = redact(content)
        self.items.append({"ref": ref, "kind": kind, "title": title, "content": content[:2500], "source": source})
        return ref

    @property
    def refs(self) -> set[str]:
        return {i["ref"] for i in self.items}

    def render(self) -> str:
        return "\n\n".join(f"[{i['ref']}] ({i['kind']}) {i['title']}\n{i['content']}" for i in self.items)

    def to_dict(self) -> dict:
        return {"items": self.items, "total_chars": sum(len(i["content"]) for i in self.items)}


def source_excerpt(root: Path | None, rel: str | None, line: int | None) -> str | None:
    if not root or not rel or not line:
        return None
    path = (Path(root) / rel).resolve()
    if Path(root).resolve() not in path.parents or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start = max(0, line - 1)
    # include decorators directly above
    while start > 0 and lines[start - 1].lstrip().startswith("@"):
        start -= 1
    out = []
    indent = None
    for i in range(start, min(len(lines), start + MAX_EXCERPT_LINES)):
        text = lines[i]
        if i > line and text.strip() and indent is not None and (len(text) - len(text.lstrip())) <= indent \
                and not text.lstrip().startswith((")", "]", "}")):
            break
        if i == line - 1:
            indent = len(text) - len(text.lstrip())
        out.append(f"{i + 1:>4} | {text}")
    return redact("\n".join(out))[0]


def _edge_line(e: dict) -> str:
    ev = (e.get("evidence") or [{}])[0]
    loc = f"{ev.get('file')}:{ev.get('line')}" if ev.get("file") else "n/a"
    detail = f" ({ev['detail']})" if ev.get("detail") else ""
    return f"{e['source']} --{e['type']}/{e['classification']}--> {e['target']} at {loc}: {ev.get('snippet', '')}{detail}"


def architecture_package(project: dict, comps: list[dict], edges: list[dict], root: Path | None) -> Package:
    pkg = Package()
    by_type: dict[str, list[dict]] = {}
    for c in comps:
        by_type.setdefault(c["type"], []).append(c)
    counts = ", ".join(f"{k}: {len(v)}" for k, v in sorted(by_type.items()))
    pkg.add("summary", f"Project '{project['name']}' component counts", counts)
    routes = by_type.get("route", [])
    if routes:
        pkg.add("routes", "HTTP routes and handlers",
                "\n".join(f"{r['name']} -> {r['data'].get('handler')} ({r['file']}:{r['line']})" for r in routes))
    resources = [c for c in comps if c["type"] in ("database", "external_api", "env_var", "worker", "app")]
    for r in resources:
        inbound = [e for e in edges if e["target"] == r["id"] and e["type"] != "contains"][:6]
        pkg.add("resource", f"{r['type']} {r['name']} (criticality {r['metrics'].get('criticality')}, "
                            f"{r['metrics'].get('dependents')} dependents)",
                "\n".join(_edge_line(e) for e in inbound) or "no inbound edges")
    top = sorted([c for c in comps if c["type"] == "function"], key=lambda c: -c["metrics"].get("criticality", 0))[:6]
    for c in top:
        out = [e for e in edges if e["source"] == c["id"] and e["type"] not in ("contains",)][:6]
        excerpt = source_excerpt(root, c.get("file"), c.get("line"))
        pkg.add("component", f"{c['id']} (criticality {c['metrics'].get('criticality')})",
                "\n".join(_edge_line(e) for e in out) + (f"\nSource:\n{excerpt}" if excerpt else ""), c["id"])
    return pkg


def prediction_package(prediction: dict, comps_by_id: dict, edges: list[dict], root: Path | None) -> Package:
    impact = prediction["impact"]
    pkg = Package()
    t = impact["target"]
    pkg.add("scenario", f"Scenario: {impact['scenario_label']} on {t['name']} ({t['id']})",
            f"Injected fault that an experiment would use: {impact['fault_description']}\n"
            f"Deterministic summary: {impact['summary']}\nMethod: {impact['method']}")
    edge_index = {(e["source"], e["target"]): e for e in edges if e["type"] != "contains"}
    seen_pairs = set()
    for a in impact["affected"][:8]:
        hops = list(zip(a["path"], a["path"][1:]))
        lines = []
        for s, d in hops:
            e = edge_index.get((s, d))
            if e and (s, d) not in seen_pairs:
                seen_pairs.add((s, d))
                lines.append(_edge_line(e))
        guard = f"\nError handled at {a['handled_at']}" if a.get("handled_at") else ""
        pkg.add("impact_path", f"{a['certainty']} impact on {a['id']} (distance {a['distance']})",
                "Path: " + " -> ".join(a["path"]) + ("\n" + "\n".join(lines) if lines else "") + guard, a["id"])
    first_hop = [a for a in impact["affected"] if a["distance"] == 1][:3]
    for a in first_hop:
        c = comps_by_id.get(a["id"])
        excerpt = source_excerpt(root, c.get("file") if c else None, c.get("line") if c else None)
        if excerpt:
            pkg.add("source", f"Source of {a['id']} (directly uses the failing component)", excerpt, a["id"])
    pkg.add("tests", "Per-test deterministic predictions",
            "\n".join(f"{x['prediction']}: {x['nodeid']}" for x in impact["tests"]) or "no tests discovered")
    return pkg


def experiment_package(experiment: dict, prediction: dict) -> Package:
    pkg = Package()
    comp = experiment["comparison"]
    obs = experiment["observation"]
    impact = prediction["impact"]
    pkg.add("scenario", f"Experiment for {impact['scenario_label']} on {impact['target']['name']}",
            f"Execution mode: {experiment['mode']}. Fault applied: {', '.join(obs.get('faulted', {}).get('fault_state', {}).get('applied', []))}. "
            f"Fault triggers observed: {comp.get('metrics', {}).get('fault_triggers')}.")
    pkg.add("verdict", f"Deterministic comparison status: {comp.get('status')}",
            f"{comp.get('reason')}\nMetrics: {comp.get('metrics')}\nNotes: {comp.get('notes')}")
    for row in comp.get("rows", [])[:20]:
        msg = re.sub(r"\s+", " ", row.get("message") or "")[-400:]
        pkg.add("test_result", f"{row['nodeid']}: predicted {row['prediction']}, observed {row['observed']} ({row['verdict']})",
                f"Dependency path: {' -> '.join(row.get('path') or []) or 'none'}\n"
                f"Handled at: {row.get('handled_at')}\nFailure message: {msg or 'n/a'}")
    return pkg


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


RESOURCE_SCENARIO = {"database": "db_unavailable", "external_api": "external_api_unavailable",
                     "env_var": "missing_env_var", "worker": "worker_unavailable"}


def chat_package(question: str, project: dict, comps: list[dict], edges: list[dict], root: Path | None, graph=None) -> Package:
    pkg = Package()
    by_type: dict[str, int] = {}
    for c in comps:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    pkg.add("summary", f"Project '{project['name']}' overview",
            f"Component counts: {by_type}\nRoutes: " + ", ".join(c["name"] for c in comps if c["type"] == "route")
            + "\nResources: " + ", ".join(f"{c['type']}:{c['name']}" for c in comps
                                          if c["type"] in ("database", "external_api", "env_var", "worker")))
    words = {w.lower() for w in _WORD.findall(question)} - {"the", "and", "what", "does", "which", "how", "why", "this", "that", "with", "for", "are", "happens", "when"}

    def score(c):
        hay = (c["id"] + " " + c["name"] + " " + (c.get("file") or "")).lower()
        s = sum(3 if w in c["name"].lower() else 1 for w in words if w in hay)
        return s + (0.001 * c["metrics"].get("criticality", 0))

    ranked = sorted((c for c in comps if c["type"] not in ("module",)), key=score, reverse=True)
    picked = [c for c in ranked if score(c) >= 1][:6] or sorted(comps, key=lambda c: -c["metrics"].get("criticality", 0))[:4]
    for c in picked:
        rel = [e for e in edges if (e["source"] == c["id"] or e["target"] == c["id"]) and e["type"] != "contains"][:8]
        excerpt = source_excerpt(root, c.get("file"), c.get("line")) if c["type"] in ("function", "class") else None
        pkg.add("component", f"{c['type']} {c['id']} metrics={c['metrics']}",
                "\n".join(_edge_line(e) for e in rel) + (f"\nSource:\n{excerpt}" if excerpt else ""), c["id"])
        # For resources, add the deterministic list of routes that depend on them (with paths).
        if graph is not None and c["type"] in RESOURCE_SCENARIO:
            from app.graph.engine import compute_impact

            try:
                impact = compute_impact(graph, RESOURCE_SCENARIO[c["type"]], c["id"])
            except ValueError:
                continue
            routes = [a for a in impact["affected"] if a["type"] == "route"]
            pkg.add("dependents", f"Routes that depend on {c['id']} (deterministic graph analysis)",
                    "\n".join(f"{a['certainty']}: {a['name']} via {' -> '.join(a['path'])}"
                              + (f" (error handled at {a['handled_at']['file']}:{a['handled_at']['line']})" if a.get("handled_at") else "")
                              for a in routes) or "No routes depend on it.", c["id"])
    return pkg


def discrepancy_package(prediction: dict, experiment: dict, comps_by_id: dict, edges: list[dict], root: Path | None) -> Package:
    pkg = Package()
    comp = experiment.get("comparison", {})
    obs = experiment.get("observation", {})
    impact = prediction.get("impact", {})
    target = impact.get("target", {})
    applied = obs.get("faulted", {}).get("fault_state", {}).get("applied", [])

    pkg.add(
        "scenario",
        f"Experiment for {impact.get('scenario_label', '')} on {target.get('name', '')} ({target.get('id', '')})",
        f"Injected fault: {impact.get('fault_description', '')}\n"
        f"Execution mode: {experiment.get('mode', '')}. Fault applied: {', '.join(applied) or 'none'}. "
        f"Fault triggers observed: {comp.get('metrics', {}).get('fault_triggers', 0)}.",
    )
    pkg.add(
        "verdict",
        f"Deterministic comparison status: {comp.get('status', '')}",
        f"Reason: {comp.get('reason', '')}\nMetrics: {comp.get('metrics', {})}\nNotes: {comp.get('notes', '')}",
    )

    rows = comp.get("rows", [])
    tests_by_nodeid = {t.get("nodeid"): t for t in impact.get("tests", []) if t.get("nodeid")}
    mismatches = [r for r in rows if r.get("verdict") == "mismatch"]
    matches = [r for r in rows if r.get("verdict") == "match"]

    for r in mismatches:
        msg = re.sub(r"\s+", " ", r.get("message") or "")[-400:]
        pkg.add(
            "mismatched_test",
            f"DISCREPANCY: {r['nodeid']} predicted {r.get('prediction')}, observed {r.get('observed')}",
            f"Verdict: mismatch\n"
            f"Dependency path: {' -> '.join(r.get('path') or []) or 'none'}\n"
            f"Handled at: {r.get('handled_at') or 'unhandled'}\n"
            f"Output/Failure message: {msg or 'none (test passed)'}",
            r["nodeid"],
        )
        test_info = tests_by_nodeid.get(r["nodeid"])
        if test_info and test_info.get("file") and test_info.get("line"):
            excerpt = source_excerpt(root, test_info["file"], test_info["line"])
            if excerpt:
                pkg.add("test_source", f"Source code of mismatched test {r['nodeid']}", excerpt, r["nodeid"])

    if matches:
        summary_lines = [
            f"{r['nodeid']}: predicted {r.get('prediction')}, observed {r.get('observed')}"
            for r in matches[:6]
        ]
        pkg.add("matched_tests", f"Confirmed tests matching prediction ({len(matches)} total)", "\n".join(summary_lines))

    affected_routes = [a for a in impact.get("affected", []) if a.get("type") == "route"]
    for route in affected_routes[:4]:
        excerpt = source_excerpt(root, route.get("file"), route.get("line"))
        route_text = (
            f"Route: {route.get('name')}\n"
            f"Path to fault: {' -> '.join(route.get('path') or [])}\n"
            f"Certainty: {route.get('certainty')}"
        )
        if excerpt:
            route_text += f"\nSource excerpt:\n{excerpt}"
        pkg.add("affected_route", f"Affected route {route.get('name')}", route_text, route.get("id"))

    target_comp = comps_by_id.get(target.get("id"))
    if target_comp and target_comp.get("file") and target_comp.get("line"):
        excerpt = source_excerpt(root, target_comp["file"], target_comp["line"])
        if excerpt:
            pkg.add("target_source", f"Fault target source {target.get('id')}", excerpt, target.get("id"))
    first_hops = [a for a in impact.get("affected", []) if a.get("distance") == 1][:2]
    for hop in first_hops:
        hop_comp = comps_by_id.get(hop.get("id"))
        if hop_comp and hop_comp.get("file") and hop_comp.get("line"):
            excerpt = source_excerpt(root, hop_comp["file"], hop_comp["line"])
            if excerpt and not any(i.get("source") == hop.get("id") for i in pkg.items):
                pkg.add("target_caller_source", f"Component calling fault target: {hop.get('id')}", excerpt, hop.get("id"))

    for r in mismatches:
        path = r.get("path") or []
        for node_id in path:
            if node_id.startswith(("func:", "class:")) and len(pkg.items) < 35:
                comp_obj = comps_by_id.get(node_id)
                if comp_obj and comp_obj.get("file") and comp_obj.get("line"):
                    if not any(i.get("source") == node_id for i in pkg.items):
                        excerpt = source_excerpt(root, comp_obj["file"], comp_obj["line"])
                        if excerpt:
                            pkg.add("path_component_source", f"Handler/service on path: {node_id}", excerpt, node_id)

    return pkg
