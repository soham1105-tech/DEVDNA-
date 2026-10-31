"""Runtime trace validation: upload traces, flag unobserved static edges and
runtime-only edges as findings. Uses the analysed sample project.
"""
import json
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Edge
from app.services.traces import ComponentIndex, parse_trace_bytes, trace_findings

TRACE_RULES = {"static_edge_unobserved", "runtime_edge_missing_from_model"}


def _latest_run_id(client, sample_project) -> str:
    detail = client.get(f"/api/projects/{sample_project}").json()
    return detail["latest_run"]["id"]


def _comparable_edges(run_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.scalars(select(Edge).where(Edge.run_id == run_id)).all()
        comparable = {"calls", "calls_http", "uses_database", "handled_by"}
        return [{"source": e.source, "target": e.target, "type": e.type}
                for e in rows if e.type in comparable]
    finally:
        db.close()


def test_parse_simple_and_otel_formats():
    simple = parse_trace_bytes(json.dumps(
        [{"caller": "a", "callee": "b"}, {"from": "b", "to": "c"}, {"from": "b", "to": "c"}]).encode())
    assert simple["format"] == "simple"
    assert simple["edges"] == [{"caller": "a", "callee": "b", "count": 1},
                               {"caller": "b", "callee": "c", "count": 2}]

    otel = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {"spanId": "1", "name": "GET /orders", "attributes": [
                                {"key": "http.request.method", "value": {"stringValue": "GET"}},
                                {"key": "http.route", "value": {"stringValue": "/orders"}},
                            ]},
                            {"spanId": "2", "parentSpanId": "1", "name": "place_order", "attributes": [
                                {"key": "code.namespace", "value": {"stringValue": "shoplite.orders"}},
                                {"key": "code.function", "value": {"stringValue": "place_order"}},
                            ]},
                            {"spanId": "3", "parentSpanId": "2", "name": "SELECT orders", "attributes": [
                                {"key": "db.system", "value": {"stringValue": "sqlite"}},
                            ]},
                        ],
                    },
                ],
            },
        ],
    }
    parsed = parse_trace_bytes(json.dumps(otel).encode())
    assert parsed["format"] == "opentelemetry"
    assert {"caller": "GET /orders", "callee": "shoplite.orders.place_order", "count": 1} in parsed["edges"]
    assert {"caller": "shoplite.orders.place_order", "callee": "database:sqlite", "count": 1} in parsed["edges"]


def test_parse_rejects_garbage():
    import pytest

    from app.services.traces import TraceValidationError

    with pytest.raises(TraceValidationError):
        parse_trace_bytes(b"not json")
    with pytest.raises(TraceValidationError):
        parse_trace_bytes(b'{"foo": 1}')
    with pytest.raises(TraceValidationError):
        parse_trace_bytes(b"")


def test_half_covered_trace_flags_the_other_half(client, sample_project):
    """A trace covering half the static edges -> exactly the other half unobserved."""
    run_id = _latest_run_id(client, sample_project)
    comparable = _comparable_edges(run_id)
    assert len(comparable) >= 6
    half = comparable[::2]  # deterministic half
    trace = {"edges": [{"caller": e["source"], "callee": e["target"]} for e in half]}

    res = client.post(f"/api/runs/{run_id}/traces", json=trace)
    assert res.status_code == 201, res.text
    cmp = res.json()["comparison"]
    assert cmp["static_comparable_edges"] == len(comparable)
    assert cmp["observed_static_edges"] == len(half)
    assert cmp["unobserved_static_edges"] == len(comparable) - len(half)
    assert cmp["coverage"] == round(len(half) / len(comparable), 3)

    unobserved_pairs = {(u["source"], u["target"]) for u in cmp["unobserved"]}
    assert unobserved_pairs == {(e["source"], e["target"]) for e in comparable} - \
        {(e["source"], e["target"]) for e in half}

    rules = {f["rule"] for f in res.json()["findings"]}
    assert "static_edge_unobserved" in rules
    assert "runtime_edge_missing_from_model" not in rules  # fully explainable by coverage


def test_runtime_only_edge_becomes_a_finding(client, sample_project):
    run_id = _latest_run_id(client, sample_project)
    trace = {"edges": [{"caller": "shoplite.main.lifespan",
                        "callee": "shoplite.services.pricing.compute_total"}]}
    res = client.post(f"/api/runs/{run_id}/traces", json=trace)
    assert res.status_code == 201
    body = res.json()
    cmp = body["comparison"]
    assert len(cmp["runtime_only_edges"]) == 1
    assert cmp["runtime_only_edges"][0]["runtime_only"] is True

    finding = next(f for f in body["findings"] if f["rule"] == "runtime_edge_missing_from_model")
    assert finding["severity"] == "medium"
    assert "lifespan" in finding["description"]

    # persisted and visible through the normal findings endpoint
    listed = client.get(f"/api/projects/{sample_project}/findings").json()
    assert {"static_edge_unobserved", "runtime_edge_missing_from_model"} <= {f["rule"] for f in listed}


def test_unmapped_names_are_counted_not_flagged(client, sample_project):
    run_id = _latest_run_id(client, sample_project)
    trace = {"edges": [{"caller": "no.such.thing", "callee": "also.nothere"}]}
    cmp = client.post(f"/api/runs/{run_id}/traces", json=trace).json()["comparison"]
    assert cmp["unmapped_count"] == 1
    assert cmp["runtime_only_edges"] == []
    assert cmp["unobserved_static_edges"] == cmp["static_comparable_edges"]


def test_reupload_replaces_previous_trace_findings(client, sample_project):
    run_id = _latest_run_id(client, sample_project)
    edge = _comparable_edges(run_id)[0]
    first = client.post(f"/api/runs/{run_id}/traces", json={
        "edges": [{"caller": edge["source"], "callee": edge["target"]},
                  {"caller": "shoplite.main.lifespan", "callee": "shoplite.services.pricing.compute_total"}]})
    assert len(first.json()["findings"]) == 2
    second = client.post(f"/api/runs/{run_id}/traces", json={
        "edges": [{"caller": edge["source"], "callee": edge["target"]}]})
    rules = [f["rule"] for f in second.json()["findings"]]
    assert rules == ["static_edge_unobserved"]
    listed = client.get(f"/api/projects/{sample_project}/findings").json()
    assert "runtime_edge_missing_from_model" not in {f["rule"] for f in listed}


def test_trace_upload_error_paths(client):
    assert client.post("/api/runs/does_not_exist/traces", json={"edges": []}).status_code == 404
    res = client.post("/api/runs/does_not_exist/traces", json={"edges": []})
    assert res.json()["error"]["code"] == "run_not_found"


def test_multipart_otel_upload(client, sample_project, tmp_path: Path):
    run_id = _latest_run_id(client, sample_project)
    comparable = _comparable_edges(run_id)
    calls = [e for e in comparable if e["type"] == "calls"]
    assert calls, "sample should have function-to-function call edges"
    edge = calls[0]
    db = SessionLocal()
    try:
        from app.models import Component

        rows = db.scalars(select(Component).where(Component.run_id == run_id,
                                                  Component.id.in_([edge["source"], edge["target"]]))).all()
        comp = {r.id: r for r in rows}
    finally:
        db.close()
    assert edge["source"] in comp and edge["target"] in comp
    src, dst = comp[edge["source"]], comp[edge["target"]]

    def span(sid, parent, name, ns=None, fn=None):
        attrs = []
        if ns:
            attrs.append({"key": "code.namespace", "value": {"stringValue": ns}})
        if fn:
            attrs.append({"key": "code.function", "value": {"stringValue": fn}})
        return {"spanId": sid, "parentSpanId": parent, "name": name, "attributes": attrs}

    # function -> function via code.namespace/code.function attributes
    payload = {
        "resourceSpans": [
            {"scopeSpans": [
                {"spans": [
                    span("1", None, src.name.split(".")[-1],
                         ns=(src.data or {}).get("module"), fn=src.name.split(".")[-1]),
                    span("2", "1", dst.name.split(".")[-1],
                         ns=(dst.data or {}).get("module"), fn=dst.name.split(".")[-1]),
                ]},
            ]},
        ],
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload))
    res = client.post(f"/api/runs/{run_id}/traces", files={"file": ("trace.json", path.read_bytes(), "application/json")})
    assert res.status_code == 201, res.text
    cmp = res.json()["comparison"]
    assert cmp["trace_format"] == "opentelemetry"
    assert cmp["observed_static_edges"] >= 1


def test_component_index_matching_variants():
    components = [
        {"id": "func:shoplite.db.get_connection", "name": "get_connection",
         "file": None, "line": 1, "type": "function", "data": {"module": "shoplite.db"}},
        {"id": "route:GET /health", "name": "GET /health", "file": None, "line": 1,
         "type": "route", "data": {"method": "GET", "path": "/health"}},
        {"id": "database:sqlite", "name": "sqlite database", "file": None, "line": None,
         "type": "database", "data": {"engine": "sqlite"}},
    ]
    idx = ComponentIndex(components)
    assert idx.resolve_one("shoplite.db.get_connection") == "func:shoplite.db.get_connection"
    assert idx.resolve_one("get_connection") == "func:shoplite.db.get_connection"
    assert idx.resolve_one("func:shoplite.db.get_connection") == "func:shoplite.db.get_connection"
    assert idx.resolve_one("GET /health") == "route:GET /health"
    assert idx.resolve_one("database:sqlite") == "database:sqlite"
    assert idx.resolve_one("nope") == set() or idx.resolve_one("nope") is None


def test_trace_findings_content_requirements():
    components = [
        {"id": "func:m.f", "name": "f", "file": "m.py", "line": 3, "type": "function", "data": {}},
        {"id": "func:m.g", "name": "g", "file": "m.py", "line": 7, "type": "function", "data": {}},
    ]
    comparison = {
        "unobserved": [{"source": "func:m.f", "target": "func:m.g", "type": "calls",
                        "source_name": "f", "target_name": "g"}],
        "runtime_only_edges": [{"caller": "func:m.f", "callee": "func:m.g", "count": 3,
                                "caller_ids": ["func:m.f"], "callee_ids": ["func:m.g"], "runtime_only": True}],
        "static_comparable_edges": 2, "observed_static_edges": 1,
    }
    findings = trace_findings(comparison, components)
    rules = {f["rule"]: f for f in findings}
    assert set(rules) == TRACE_RULES
    un = rules["static_edge_unobserved"]
    assert un["severity"] == "low" and "m.py" in json.dumps(un["evidence"])
    rt = rules["runtime_edge_missing_from_model"]
    assert rt["severity"] == "medium" and rt["confidence"] == "high"
