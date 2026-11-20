"""Deterministic findings derived from the analysed graph.

Severity rules (documented in README):
  * Shared-dependency findings use route_share = routes definitely affected / total routes:
        high >= 0.5, medium >= 0.2, low > 0.
  * Startup dependency on a database/external API is high (the whole app cannot start).
  * Hard-coded secrets are high; required env vars and unguarded outbound HTTP are medium;
    missing tests, missing timeouts on httpx (5 s default) and broad excepts are low.
Confidence: high when every edge on the evidence path is 'confirmed'; medium otherwise.
"""
from __future__ import annotations

from app.graph.engine import compute_impact

RESOURCE_SCENARIO = {"database": "db_unavailable", "external_api": "external_api_unavailable",
                     "env_var": "missing_env_var", "worker": "worker_unavailable"}


def _confidence(g, path: list[str]) -> str:
    for a, b in zip(path, path[1:]):
        for _, _, data in g.edges(a, data=True):
            if data["target"] == b and data["classification"] != "confirmed":
                return "medium"
    return "high"


def _path_evidence(g, path: list[str]) -> list[dict]:
    out = []
    for a, b in zip(path, path[1:]):
        for _, _, data in g.edges(a, data=True):
            if data["target"] == b and data["type"] != "contains":
                ev = (data.get("evidence") or [{}])[0]
                out.append({**ev, "relationship": f"{a} --{data['type']}--> {b}", "classification": data["classification"]})
                break
    return out


def generate_findings(g, result: dict) -> list[dict]:
    findings: list[dict] = []
    routes_total = sum(1 for _, d in g.nodes(data=True) if d["type"] == "route")
    apps = [n for n, d in g.nodes(data=True) if d["type"] == "app"]

    for n, d in g.nodes(data=True):
        scenario = RESOURCE_SCENARIO.get(d["type"])
        if not scenario:
            continue
        try:
            impact = compute_impact(g, scenario, n)
        except ValueError:
            continue
        definite = {a["id"]: a for a in impact["affected"] if a["certainty"] == "definite"}
        for app_id in apps:
            if app_id in definite and d["type"] in ("database", "external_api", "worker"):
                path = definite[app_id]["path"]
                findings.append({
                    "rule": "startup_dependency", "severity": "high", "component_id": n,
                    "title": f"Application startup depends on {d['name']}",
                    "description": f"{g.nodes[app_id]['name']} reaches {d['name']} during startup without error handling. "
                                   f"If it is unavailable the application cannot start, so every route fails.",
                    "evidence": _path_evidence(g, path), "confidence": _confidence(g, path),
                    "assumptions": ["Startup handlers run before the first request (FastAPI lifespan / startup events)."],
                    "mitigation": "Make startup resilient (retry with backoff, lazy initialisation) and report readiness "
                                  "separately from liveness.",
                })
        routes_def = [a for a in definite.values() if a["type"] == "route"]
        if routes_def and routes_total:
            share = len(routes_def) / routes_total
            severity = "high" if share >= 0.5 else "medium" if share >= 0.2 else "low"
            sample = routes_def[0]["path"]
            findings.append({
                "rule": "shared_dependency", "severity": severity, "component_id": n,
                "title": f"{len(routes_def)} of {routes_total} routes depend on {d['name']}",
                "description": f"A failure of {d['name']} propagates without being handled to {len(routes_def)} HTTP routes "
                               f"({share:.0%} of discovered routes): "
                               + ", ".join(r["name"] for r in routes_def[:6]) + ("…" if len(routes_def) > 6 else ""),
                "evidence": _path_evidence(g, sample), "confidence": _confidence(g, sample),
                "assumptions": ["Route share uses statically discovered routes only.",
                                "'Definite' paths may still be cut short at runtime by validation or early returns."],
                "mitigation": "Add explicit error handling at the boundary (e.g. return 503 with a clear message), "
                              "timeouts and retries, and a test that simulates this failure.",
            })
        tests_cov = [t for t in impact["tests"] if t["prediction"] != "pass"]
        if not tests_cov:
            findings.append({
                "rule": "untested_failure_path", "severity": "low", "component_id": n,
                "title": f"No test exercises code that depends on {d['name']}",
                "description": "No discovered test reaches this dependency, so its failure behaviour is unverified.",
                "evidence": [], "confidence": "medium",
                "assumptions": ["Test discovery is static; tests created dynamically are not seen."],
                "mitigation": "Add a test that exercises a code path using this dependency.",
            })

    sig = result.get("signals", {})
    for r in sig.get("env_required", []):
        findings.append({
            "rule": "required_env_var", "severity": "medium", "component_id": f"env:{r['var']}",
            "title": f"{r['var']} is required with no default",
            "description": f"`{r['component'].removeprefix('func:')}` reads {r['var']} with os.environ[...] — a missing value raises "
                           f"KeyError at call time instead of failing fast at startup.",
            "evidence": [{k: r[k] for k in ("file", "line", "snippet")}], "confidence": "high",
            "assumptions": ["The variable may be provided by deployment configuration not visible in the repository."],
            "mitigation": "Validate required configuration at startup and fail with a clear error message.",
        })
    for h in sig.get("http_calls", []):
        if not h["timeout"]:
            sev = "medium" if h["lib"].startswith(("requests", "urllib")) else "low"
            findings.append({
                "rule": "http_without_timeout", "severity": sev, "component_id": h["component"],
                "title": f"Outbound HTTP call without an explicit timeout ({h['lib']})",
                "description": "requests and urllib wait indefinitely by default; httpx defaults to 5 s. Slow upstreams "
                               "can exhaust workers.",
                "evidence": [{k: h[k] for k in ("file", "line", "snippet")}], "confidence": "high",
                "assumptions": ["A timeout may be configured globally in a way static analysis cannot see."],
                "mitigation": "Pass an explicit timeout and handle timeout errors.",
            })
        if not h["guarded"]:
            findings.append({
                "rule": "http_unhandled", "severity": "medium", "component_id": h["component"],
                "title": "Outbound HTTP call has no local error handling",
                "description": "Connection errors from this call propagate to callers unhandled.",
                "evidence": [{k: h[k] for k in ("file", "line", "snippet")}], "confidence": "high",
                "assumptions": ["Callers may handle the error; check the impact analysis for propagation."],
                "mitigation": "Catch transport errors, translate them to a domain error and decide on fallback behaviour.",
            })
    tested = {e["target"] for e in result["edges"] if e["type"] == "exercises"}
    for n, d in g.nodes(data=True):
        if d["type"] == "route" and n not in tested:
            findings.append({
                "rule": "untested_route", "severity": "low", "component_id": n,
                "title": f"Route {d['name']} has no matching test",
                "description": "No test-client call in the test suite matches this route.",
                "evidence": [{"file": d.get("file"), "line": d.get("line"), "snippet": ""}], "confidence": "medium",
                "assumptions": ["Tests that build URLs dynamically are not matched."],
                "mitigation": "Add an API test for this route, including a failure case.",
            })
    for s in result.get("secret_hits", []):
        norm_file = (s.get("file") or "").replace("\\", "/").lower()
        is_non_prod = any(
            marker in norm_file
            for marker in ("tests/", "/tests/", "test_", "_test.", "fixtures/", "/fixtures/", "examples/", "docs/", ".example")
        )
        findings.append({
            "rule": "hardcoded_secret",
            "severity": "low" if is_non_prod else "high",
            "component_id": None,
            "title": f"Possible hard-coded secret in {s['file']}",
            "description": "A value that looks like a credential is present in source. The value is redacted and was "
                           "never shown or sent anywhere.",
            "evidence": [s], "confidence": "medium",
            "assumptions": [
                "Pattern-based detection can produce false positives.",
                *(["Detected in a test, fixture, or example path; verify whether this is a dummy credential or a real secret."] if is_non_prod else []),
            ],
            "mitigation": "Move the value to a secret manager or environment variable and rotate it.",
        })
    for b in sig.get("broad_excepts", []):
        findings.append({
            "rule": "swallowed_exception", "severity": "low", "component_id": b["component"],
            "title": "Broad exception handler swallows errors",
            "description": "An `except Exception`/bare except whose body does nothing can hide failures.",
            "evidence": [{k: b[k] for k in ("file", "line", "snippet")}], "confidence": "high",
            "assumptions": [], "mitigation": "Catch specific exceptions and log or re-raise.",
        })
    for p in result["coverage"].get("parse_errors", []):
        findings.append({
            "rule": "parse_error", "severity": "info", "component_id": None,
            "title": f"Could not parse {p['file']}", "description": f"Syntax error: {p['error']}. This file was not analysed.",
            "evidence": [{"file": p["file"], "line": p["line"], "snippet": ""}], "confidence": "high",
            "assumptions": [], "mitigation": "Fix the syntax error or check the Python version.",
        })
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"]))
    return findings
