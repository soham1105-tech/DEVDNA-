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


# --- regression tests for collision, merge, lambda, deep chains, unresolved ---

def _analyse(tmp_path):
    return analyse_project(tmp_path)


def test_module_name_collision_does_not_drop_either_file(tmp_path):
    """pkg.py and pkg/__init__.py map to the same module name; both must be
    analysed and the collision must be reported, never silent."""
    (tmp_path / "pkg.py").write_text("def from_flat():\n    return 1\n", encoding="utf-8")
    init = tmp_path / "pkg"
    init.mkdir()
    (init / "__init__.py").write_text("def from_init():\n    return 2\n", encoding="utf-8")
    r = _analyse(tmp_path)
    funcs = _ids(r, "function")
    assert "func:pkg.from_flat" in funcs and "func:pkg.from_init" in funcs
    collision = [e for e in r["coverage"]["parse_errors"] if "is defined by both" in e.get("error", "")]
    assert collision and "pkg.py" in collision[0]["error"] and "pkg/__init__.py" in collision[0]["error"]


def test_lambda_body_calls_produce_edges(tmp_path):
    (tmp_path / "l.py").write_text(
        "def fetch_data():\n    return 1\n"
        "def use_lambda():\n"
        "    fetch = lambda: fetch_data()\n"
        "    return fetch()\n",
        encoding="utf-8",
    )
    r = _analyse(tmp_path)
    e = _edge(r, "calls", "func:l.use_lambda", "func:l.fetch_data")
    assert e is not None and e["classification"] == "confirmed"


def test_deep_self_attribute_chain_resolves(tmp_path):
    (tmp_path / "d.py").write_text(
        "class Pool:\n"
        "    def query(self):\n        return 1\n"
        "class Database:\n"
        "    def __init__(self):\n        self.pool = Pool()\n"
        "class App:\n"
        "    def __init__(self):\n        self.db = Database()\n"
        "    def run(self):\n        return self.db.pool.query()\n",
        encoding="utf-8",
    )
    r = _analyse(tmp_path)
    e = _edge(r, "calls", "func:d.App.run", "func:d.Pool.query")
    assert e is not None and e["classification"] == "inferred"


def test_deep_module_chain_resolves(tmp_path):
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "helpers" / "inner.py").write_text(
        "class Inner:\n    def run(self):\n        return 1\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text(
        "import helpers.inner\n"
        "def go():\n    return helpers.inner.Inner.run()\n",
        encoding="utf-8",
    )
    r = _analyse(tmp_path)
    e = _edge(r, "calls", "func:caller.go", "func:helpers.inner.Inner.run")
    assert e is not None and e["classification"] in ("confirmed", "inferred")


def test_unresolved_calls_count_every_call_without_an_edge(tmp_path):
    (tmp_path / "u.py").write_text(
        "def f():\n"
        "    print('builtin stays uncounted')\n"
        "    unknown_obj.method()\n"
        "    handlers['x']()\n",
        encoding="utf-8",
    )
    r = _analyse(tmp_path)
    # print() is a builtin (never an edge, never counted); the other two calls
    # produced no edge and must both be counted
    assert r["coverage"]["unresolved_calls"] == 2
