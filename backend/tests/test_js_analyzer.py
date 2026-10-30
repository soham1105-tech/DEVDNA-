"""Tests for the tree-sitter JS/TS analyzer on the bundled js_shop fixture.

The fixture mirrors the Python sample_project: Express app + routers, a Fastify
plugin, a Postgres pool, an axios client, a fetch client, env reads and a
supertest suite. Assertions follow the same conventions as test_analyzer.py.
"""
from pathlib import Path

import pytest

from app.analyzer.js_analyzer import analyse_js_project, paths_match
from app.graph.engine import build_graph, compute_impact, compute_metrics

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "js_shop"


def _ids(result, ctype):
    return {c["id"] for c in result["components"] if c["type"] == ctype}


def _edge(result, etype, source, target):
    return next((e for e in result["edges"] if e["type"] == etype and e["source"] == source and e["target"] == target), None)


@pytest.fixture(scope="module")
def js_analysis():
    return analyse_js_project(FIXTURE, use_cache=False)


def test_discovers_express_routes_with_mount_prefixes(js_analysis):
    assert _ids(js_analysis, "route") >= {
        "route:GET /health",
        "route:GET /api/products",
        "route:GET /api/products/{id}",
        "route:POST /api/orders",
        "route:GET /api/orders/{id}",
    }


def test_discovers_fastify_plugin_routes(js_analysis):
    routes = _ids(js_analysis, "route")
    assert "route:GET /fastify/ping" in routes
    assert "route:GET /admin/users" in routes
    assert "route:POST /admin/users/{id}/roles" in routes
    assert _ids(js_analysis, "app") == {"app:server.app", "app:fastify.app"}


def test_routes_link_to_handlers_and_apps(js_analysis):
    e = _edge(js_analysis, "handled_by", "route:GET /api/products", "func:services.catalog.listProducts")
    assert e is not None and e["classification"] == "confirmed"
    assert _edge(js_analysis, "handled_by", "route:POST /api/orders", "func:services.orders.createOrder")
    assert _edge(js_analysis, "served_by", "route:GET /api/products", "app:server.app")["classification"] == "confirmed"


def test_imports_resolve_to_module_edges(js_analysis):
    assert _edge(js_analysis, "imports", "module:server", "module:routes.products") is not None
    assert _edge(js_analysis, "imports", "module:services.orders", "module:clients.shipping") is not None


def test_cross_module_calls(js_analysis):
    e = _edge(js_analysis, "calls", "func:services.orders.createOrder", "func:repositories.orders.insertOrder")
    assert e is not None and e["classification"] == "confirmed"
    # instance method through `new ShippingClient()` -> inferred
    assert _edge(js_analysis, "calls", "func:services.orders.createOrder",
                 "func:clients.shipping.ShippingClient.notify")["classification"] == "inferred"


def test_http_clients_become_external_apis(js_analysis):
    externals = _ids(js_analysis, "external_api")
    assert "external:catalog.internal" in externals          # axios.create baseURL
    assert "external:CATALOG_BASE_URL" in externals          # env-derived base URL
    assert "external:SHIPPING_API_URL" in externals          # class attribute from process.env
    assert _edge(js_analysis, "calls_http", "func:services.catalog.getProduct", "external:catalog.internal")
    http = js_analysis["signals"]["http_calls"]
    fetch_calls = [h for h in http if h["lib"] == "fetch"]
    assert any(h["timeout"] for h in fetch_calls)      # AbortSignal.timeout in options
    assert any(not h["timeout"] for h in fetch_calls)  # bare fetch without options
    axios_get = next(h for h in http if h["lib"] == "axios.get")
    assert axios_get["timeout"] is True                # axios.create({timeout})


def test_database_usage(js_analysis):
    assert _ids(js_analysis, "database") == {"database:postgresql"}
    assert _edge(js_analysis, "uses_database", "func:repositories.orders.insertOrder", "database:postgresql")
    assert _edge(js_analysis, "uses_database", "func:services.catalog.listProducts", "database:postgresql")


def test_env_reads(js_analysis):
    assert {"env:SHIPPING_API_URL", "env:CATALOG_BASE_URL", "env:DATABASE_URL"} <= _ids(js_analysis, "env_var")
    e = _edge(js_analysis, "reads_env", "func:clients.shipping.ShippingClient.constructor", "env:SHIPPING_API_URL")
    assert e is not None
    assert {"SHIPPING_API_URL", "CATALOG_BASE_URL"} <= {s["var"] for s in js_analysis["signals"]["env_required"]}


def test_supertest_tests_exercise_routes(js_analysis):
    assert _edge(js_analysis, "exercises", "test:tests/api.test.js::POST /api/orders creates an order",
                 "route:POST /api/orders") is not None
    assert js_analysis["coverage"]["routes_with_tests"] >= 3


def test_result_shape_matches_python_analyzer_contract(js_analysis):
    for c in js_analysis["components"]:
        assert {"id", "type", "name", "file", "line", "data"} <= set(c)
    for e in js_analysis["edges"]:
        assert {"id", "source", "target", "type", "classification", "evidence", "call_sites"} <= set(e)
        assert e["classification"] in ("confirmed", "inferred", "unknown")
        assert e["evidence"] and e["evidence"][0]["file"]
    assert {"file_count", "languages", "analysed_languages", "manifests"} <= set(js_analysis["scan"])
    assert js_analysis["scan"]["analysed_languages"] == ["JavaScript", "TypeScript"]
    assert {"Express", "Fastify", "Supertest"} <= set(js_analysis["frameworks"])
    assert {"axios", "express", "pg"} <= set(js_analysis["third_party_imports"])
    assert {d["name"] for d in js_analysis["dependencies"]} >= {"axios", "express", "jest"}


def test_js_graph_works_with_engine_and_findings(js_analysis):
    g = build_graph(js_analysis["components"], js_analysis["edges"])
    metrics = compute_metrics(g)
    assert metrics["func:services.orders.createOrder"]["criticality"] > 0
    impact = compute_impact(g, "db_unavailable", "database:postgresql")
    affected = {a["id"] for a in impact["affected"]}
    assert "route:GET /api/products" in affected
    assert any(t["prediction"] == "fail" for t in impact["tests"])


def test_paths_match_express_params():
    assert paths_match("/api/orders/:orderId", "/api/orders/{x}")
    assert not paths_match("/api/orders/:id", "/api/orders/:id/items")


def test_parse_error_is_reported_not_fatal(tmp_path):
    (tmp_path / "broken.js").write_text("function oops( {")
    result = analyse_js_project(tmp_path, use_cache=False)
    assert any(p["file"] == "broken.js" for p in result["coverage"]["parse_errors"])


def test_incremental_only_reparses_changed_files(tmp_path):
    (tmp_path / "a.js").write_text("export function a() { return 1; }\n")
    (tmp_path / "b.js").write_text("export function b() { return 2; }\n")
    first = analyse_js_project(tmp_path)
    assert first["scan"]["incremental"]["files_parsed"] == 2
    (tmp_path / "b.js").write_text("export function b() { return 3; }\n")  # change one file
    second = analyse_js_project(tmp_path)
    inc = second["scan"]["incremental"]
    assert inc["files_reused"] == 1, inc
    assert inc["files_parsed"] == 1, inc
    # the changed file's edit is visible in the new result
    assert any(f["name"] == "b" for f in
               [{"name": c["name"]} for c in second["components"]])
