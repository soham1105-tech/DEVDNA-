"""Runtime trace validation: compare an uploaded trace file against the static graph.

Accepted formats (auto-detected):
  * Simple JSON: a list (or {"edges": [...]}) of {"caller": ..., "callee": ...}
    pairs observed in production. "from"/"to" and "source"/"target" are also
    accepted as key aliases.
  * OpenTelemetry JSON export: {"resourceSpans": [{"scopeSpans": [{"spans":
    [...]}]}]} - caller/callee edges are derived from span parent/child
    relationships; span names are qualified with code.namespace /
    code.filepath when present, and http spans are normalised to
    "METHOD /route" route names.

The comparison is name-based: trace names are matched to static component ids
by route name, qualified function name, class.method, or module name. Edges
whose endpoints cannot be mapped to any component are counted as unmapped, not
silently dropped.

Result: for the comparable static edge types (calls, calls_http,
uses_database, handled_by) every edge either has runtime evidence or is
reported as unobserved; runtime edges with no static counterpart are reported
as runtime-only. Both become findings (see trace_findings).
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from urllib.parse import urlparse

log = logging.getLogger("devdna.traces")

COMPARABLE_EDGES = {"calls", "calls_http", "uses_database", "handled_by"}
MAX_EDGES = 200_000
MAX_SPANS = 500_000


class TraceValidationError(ValueError):
    """Raised when a trace file cannot be parsed. Message is safe to show users."""


# --------------------------------------------------------------------------- parsing
def _attr_value(attr: dict):
    """Read an OTel attribute value: {"key": ..., "value": {"stringValue": ...}}."""
    if not isinstance(attr, dict):
        return None
    value = attr.get("value") if isinstance(attr.get("value"), dict) else attr
    if isinstance(value.get("stringValue"), str):
        return value["stringValue"]
    if isinstance(value.get("boolValue"), bool):
        return str(value["boolValue"])
    if isinstance(value.get("intValue"), (int, str)):
        return str(value["intValue"])
    if isinstance(value.get("doubleValue"), (int, float)):
        return str(value["doubleValue"])
    return None


def _span_name(span: dict) -> str:
    """Normalise an OTel span to a DevDNA-style component name."""
    attrs = {}
    for kv in span.get("attributes") or []:
        v = _attr_value(kv)
        if v is not None and kv.get("key"):
            attrs[kv["key"]] = v
    name = span.get("name", "")
    ns = attrs.get("code.namespace")
    fn = attrs.get("code.function")
    if fn:
        base = f"{ns}.{fn}" if ns else fn
        if name and name != fn and not name.startswith(fn):
            return f"{base} ({name})"  # e.g. instrumented ASGI middleware
        return base
    method = attrs.get("http.request.method") or attrs.get("http.method")
    route = attrs.get("http.route") or attrs.get("http.target")
    if route:
        path = urlparse(route).path if route.startswith(("http://", "https://")) else route
        return f"{(method or 'GET').upper()} {path}"
    if attrs.get("db.system"):
        return f"database:{attrs['db.system']}"
    return name


def _parse_otel(data: dict) -> dict:
    edges: Counter = Counter()
    spans = 0
    for rs in data.get("resourceSpans") or []:
        for ss in rs.get("scopeSpans") or []:
            by_id: dict[str, dict] = {}
            for span in ss.get("spans") or []:
                spans += 1
                if spans > MAX_SPANS:
                    raise TraceValidationError(f"Trace has more than {MAX_SPANS} spans")
                sid = span.get("spanId")
                if sid:
                    by_id[sid] = span
            for span in by_id.values():
                parent = by_id.get(span.get("parentSpanId"))
                if parent is None:
                    continue
                edges[(_span_name(parent), _span_name(span))] += 1
    return {"format": "opentelemetry", "spans": spans, "edges": [{"caller": c, "callee": e, "count": n}
                                                                for (c, e), n in edges.items()]}


def _edge_item(item: dict) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    caller = item.get("caller") or item.get("from") or item.get("source")
    callee = item.get("callee") or item.get("to") or item.get("target")
    if isinstance(caller, str) and isinstance(callee, str) and caller and callee:
        return caller, callee
    return None


def parse_trace_bytes(raw: bytes) -> dict:
    """Parse an uploaded trace file into {"format", "edges", ...}."""
    if not raw or not raw.strip():
        raise TraceValidationError("Trace file is empty")
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        raise TraceValidationError(f"Trace file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) and not isinstance(data, list):
        raise TraceValidationError("Trace JSON must be an object or a list")

    if isinstance(data, dict) and "resourceSpans" in data:
        return _parse_otel(data)

    items = data if isinstance(data, list) else data.get("edges") or data.get("calls")
    if not isinstance(items, list):
        raise TraceValidationError(
            "Trace JSON must be an OpenTelemetry export (resourceSpans) or a list of "
            '{"caller": ..., "callee": ...} edges')
    edges: Counter = Counter()
    skipped = 0
    for item in items:
        pair = _edge_item(item)
        if pair is None:
            skipped += 1
            continue
        edges[pair] += 1
        if len(edges) > MAX_EDGES:
            raise TraceValidationError(f"Trace has more than {MAX_EDGES} distinct edges")
    return {"format": "simple", "spans": sum(edges.values()), "skipped_items": skipped,
            "edges": [{"caller": c, "callee": e, "count": n} for (c, e), n in edges.items()]}


# --------------------------------------------------------------------------- matching
class ComponentIndex:
    """Trace name -> component ids. Longer, more specific names win."""

    def __init__(self, components: list[dict]):
        self.by_id = {c["id"]: c for c in components}
        self._names: dict[str, set[str]] = {}
        for c in components:
            for name in self._names_of(c):
                self._names.setdefault(name.lower(), set()).add(c["id"])

    @staticmethod
    def _names_of(c: dict) -> set[str]:
        names = {c["id"], c["name"]}
        prefix, _, rest = c["id"].partition(":")
        if rest:
            names.add(rest)
        data = c.get("data") or {}
        if prefix == "func":
            names.add(data.get("module", "") + "." + rest if data.get("module") else rest)
            if "." in rest:  # Class.method
                names.add(rest.rsplit(".", 1)[1])
        if prefix == "route":
            names.add(rest)
        if prefix == "database":
            names.add(f"database:{data.get('engine', rest)}")
        return {n for n in names if n}

    def resolve(self, name: str) -> set[str]:
        return self._names.get(name.lower(), set())

    def resolve_one(self, name: str) -> str | None:
        hits = self.resolve(name)
        if len(hits) == 1:
            return next(iter(hits))
        return None  # ambiguous or unknown: keep the raw name


# --------------------------------------------------------------------------- comparison
def compare_traces(components: list[dict], edges: list[dict], trace: dict) -> dict:
    """Compare a parsed trace against the static graph of one analysis run."""
    index = ComponentIndex(components)
    static_pairs: dict[tuple[str, str], dict] = {}
    for e in edges:
        if e.get("type") in COMPARABLE_EDGES:
            static_pairs.setdefault((e["source"], e["target"]), e)

    observed: dict[tuple[str, str], int] = {}
    unmapped: list[dict] = []
    runtime_edges: list[dict] = []
    for te in trace["edges"]:
        callers = index.resolve(te["caller"])
        callees = index.resolve(te["callee"])
        count = te.get("count", 1)
        if not callers or not callees:
            unmapped.append({**te, "caller_mapped": bool(callers), "callee_mapped": bool(callees)})
            continue
        matched_any = False
        for s in callers:
            for t in callees:
                if (s, t) in static_pairs:
                    observed[(s, t)] = observed.get((s, t), 0) + count
                    matched_any = True
        if not matched_any:
            runtime_edges.append({**te,
                                  "caller_ids": sorted(callers), "callee_ids": sorted(callees),
                                  "runtime_only": True})

    comparable = list(static_pairs)
    unobserved = [eid for eid in comparable if eid not in observed]
    observed_edges = [
        {"source": s, "target": t, "type": static_pairs[(s, t)]["type"], "observed_count": n,
         "source_name": index.by_id[s]["name"], "target_name": index.by_id[t]["name"]}
        for (s, t), n in observed.items()]
    unobserved_edges = [
        {"source": s, "target": t, "type": static_pairs[(s, t)]["type"],
         "source_name": index.by_id[s]["name"], "target_name": index.by_id[t]["name"]}
        for s, t in unobserved]

    return {
        "trace_format": trace["format"],
        "trace_edges": len(trace["edges"]),
        "trace_spans": trace.get("spans", 0),
        "static_comparable_edges": len(comparable),
        "observed_static_edges": len(observed),
        "unobserved_static_edges": len(unobserved),
        "coverage": round(len(observed) / len(comparable), 3) if comparable else None,
        "observed": observed_edges,
        "unobserved": unobserved_edges,
        "runtime_only_edges": runtime_edges,
        "unmapped_trace_edges": unmapped[:200],
        "unmapped_count": len(unmapped),
    }


# --------------------------------------------------------------------------- findings
def _ev(c: dict | None) -> dict:
    return {"file": (c or {}).get("file"), "line": (c or {}).get("line"),
            "snippet": "", "detail": (c or {}).get("id", "")}


def trace_findings(comparison: dict, components: list[dict]) -> list[dict]:
    """Turn a trace comparison into findings (same shape as generate_findings)."""
    by_id = {c["id"]: c for c in components}
    findings: list[dict] = []
    unobserved = comparison["unobserved"]
    if unobserved:
        sample = unobserved[:6]
        findings.append({
            "rule": "static_edge_unobserved",
            "severity": "low",
            "component_id": None,
            "title": f"{len(unobserved)} of {comparison['static_comparable_edges']} static edges never observed in production traces",
            "description": f"The uploaded trace covers {comparison['observed_static_edges']} of "
                           f"{comparison['static_comparable_edges']} call-flow edges. These were never observed: "
                           + ", ".join(f"{u['source_name']} -> {u['target_name']} ({u['type']})" for u in sample)
                           + ("…" if len(unobserved) > 6 else "")
                           + ". Unobserved edges are dead code, unexercised paths, or traces that cover "
                             "only part of the system.",
            "evidence": [{"detail": f"{u['source']} --{u['type']}--> {u['target']}",
                          "file": by_id.get(u["source"], {}).get("file"),
                          "line": by_id.get(u["source"], {}).get("line"), "snippet": ""} for u in unobserved[:8]],
            "confidence": "medium",
            "assumptions": ["The trace window may not cover seasonal or rare code paths.",
                            "Endpoints are matched by name; dynamically dispatched calls may be missed."],
            "mitigation": "Confirm whether the code is reachable; remove dead paths or extend trace coverage.",
        })
    runtime_only = comparison["runtime_only_edges"]
    if runtime_only:
        sample = runtime_only[:6]
        findings.append({
            "rule": "runtime_edge_missing_from_model",
            "severity": "medium",
            "component_id": None,
            "title": f"{len(runtime_only)} runtime edges are missing from the static model",
            "description": "Production traces show caller -> callee relationships the static analysis cannot see: "
                           + ", ".join(f"{r['caller']} -> {r['callee']}" for r in sample)
                           + ("…" if len(runtime_only) > 6 else "")
                           + ". Causes: dynamic dispatch, reflection, config-driven wiring, or code outside "
                             "the analysed tree.",
            "evidence": [{"detail": f"{r['caller']} -> {r['callee']} (x{r.get('count', 1)})",
                          "file": by_id.get(r["caller_ids"][0], {}).get("file") if r.get("caller_ids") else None,
                          "line": None, "snippet": ""} for r in runtime_only[:8]],
            "confidence": "high",
            "assumptions": ["Both endpoints resolved to analysed components; the static graph has no edge between them."],
            "mitigation": "Make the wiring explicit (direct calls, registered handlers) so impact analysis can see it.",
        })
    return findings
