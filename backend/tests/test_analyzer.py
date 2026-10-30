from pathlib import Path

from app.analyzer.python_analyzer import analyse_project, paths_match


def _ids(result, ctype):
    return {c["id"] for c in result["components"] if c["type"] == ctype}


def _edge(result, etype, source, target):
    return next((e for e in result["edges"] if e["type"] == etype and e["source"] == source and e["target"] == target), None)


def test_discovers_routes_with_router_prefixes(sample_analysis):
    routes = _ids(sample_analysis, "route")
    assert routes == {
        "route:GET /health", "route:GET /api/products", "route:GET /api/products/{product_id}",
        "route:POST /api/orders", "route:GET /api/orders/{order_id}", "route:GET /api/shipping/quote",
    }


def test_discovers_resources(sample_analysis):
    assert _ids(sample_analysis, "database") == {"database:sqlite"}
    assert _ids(sample_analysis, "external_api") == {"external:SHIPPING_API_URL"}
    assert {"env:SHIPPING_API_URL", "env:DATABASE_PATH"} <= _ids(sample_analysis, "env_var")
    assert _ids(sample_analysis, "worker") == {"worker:shoplite.worker.NotificationWorker"}
    assert _ids(sample_analysis, "app") == {"app:shoplite.main.app"}


def test_edges_carry_evidence_with_file_and_line(sample_analysis):
    e = _edge(sample_analysis, "uses_database", "func:shoplite.db.get_connection", "database:sqlite")
    assert e["classification"] == "confirmed"
    ev = e["evidence"][0]
    assert ev["file"] == "shoplite/db.py"
    assert ev["line"] == 30
    assert "sqlite3.connect" in ev["snippet"]
    for edge in sample_analysis["edges"]:
        assert edge["classification"] in ("confirmed", "inferred", "unknown")
        assert edge["evidence"] and edge["evidence"][0]["file"]


def test_cross_module_calls_and_instance_methods(sample_analysis):
    assert _edge(sample_analysis, "calls", "func:shoplite.services.orders.place_order",
                 "func:shoplite.repositories.products.get_product")["classification"] == "confirmed"
    # `worker` is a module-level instance imported from another module -> inferred
    e = _edge(sample_analysis, "calls", "func:shoplite.services.orders.place_order", "func:shoplite.worker.NotificationWorker.submit")
    assert e["classification"] == "inferred"
    # the call is inside `try: ... except WorkerUnavailable` and the guard is recorded
    handlers = e["call_sites"][0]["tries"][0]
    assert handlers[0]["names"] == ["WorkerUnavailable"] and handlers[0]["reraises"] is False


def test_http_client_attribute_resolution_and_env_hint(sample_analysis):
    e = _edge(sample_analysis, "calls_http", "func:shoplite.clients.shipping.ShippingClient.get_quote", "external:SHIPPING_API_URL")
    assert e is not None
    env = _edge(sample_analysis, "reads_env", "func:shoplite.clients.shipping.ShippingClient.__init__", "env:SHIPPING_API_URL")
    assert env["evidence"][0]["has_default"] is False


def test_lifespan_and_route_wiring(sample_analysis):
    assert _edge(sample_analysis, "runs_on_startup", "app:shoplite.main.app", "func:shoplite.main.lifespan")
    assert _edge(sample_analysis, "served_by", "route:GET /health", "app:shoplite.main.app")["classification"] == "confirmed"


def test_tests_are_mapped_to_routes_including_fixtures(sample_analysis):
    e = _edge(sample_analysis, "exercises", "test:tests/test_api.py::test_get_order", "route:POST /api/orders")
    assert e and "via fixture 'created_order'" in e["evidence"][0]["detail"]
    assert _edge(sample_analysis, "calls", "test:tests/test_units.py::test_compute_total_without_discount",
                 "func:shoplite.services.pricing.compute_total")
    assert sample_analysis["coverage"]["routes_with_tests"] == 6
    assert sample_analysis["coverage"]["tests"] == 12


def test_frameworks_and_manifests(sample_analysis):
    assert {"FastAPI", "httpx", "sqlite3", "pytest"} <= set(sample_analysis["frameworks"])
    assert {d["name"] for d in sample_analysis["dependencies"]} == {"fastapi", "httpx", "pytest"}


def test_path_matching():
    assert paths_match("/api/orders/{order_id}", "/api/orders/{x}")
    assert paths_match("/api/products/{id}", "/api/products/2?x=1")
    assert not paths_match("/api/products", "/api/orders")


def test_flask_requests_secrets_and_parse_errors(tmp_path: Path):
    (tmp_path / "web.py").write_text(
        "import os\nimport requests\nfrom flask import Flask\n\napp = Flask(__name__)\n"
        "API_TOKEN = 'abcd1234efgh5678'\n\n"
        "@app.route('/items', methods=['GET', 'POST'])\n"
        "def items():\n    return fetch()\n\n"
        "def fetch():\n    url = os.environ['ITEMS_URL']\n    return requests.get(url).json()\n",
        encoding="utf-8")
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    r = analyse_project(tmp_path)
    assert {"route:GET /items", "route:POST /items"} <= _ids(r, "route")
    assert "external:ITEMS_URL" in _ids(r, "external_api")
    assert r["coverage"]["parse_errors"][0]["file"] == "broken.py"
    http = r["signals"]["http_calls"][0]
    assert http["timeout"] is False and http["guarded"] is False
    # the secret value never appears in any evidence snippet
    assert r["secret_hits"] and all("abcd1234efgh5678" not in h["snippet"] for h in r["secret_hits"])
    assert "abcd1234efgh5678" not in str(r["edges"])


def test_function_local_reassignment_does_not_recurse(tmp_path: Path):
    """A local `x = x.replace(...)` must not register a module instance and
    recurse forever during name resolution (RecursionError guard)."""
    (tmp_path / "util.py").write_text(
        "def scrub(msg: str) -> str:\n"
        "    msg = msg.replace('a', 'b')\n"
        "    other = other.strip() if other else msg\n"
        "    return msg\n",
        encoding="utf-8")
    r = analyse_project(tmp_path)
    assert "func:util.scrub" in _ids(r, "function")
