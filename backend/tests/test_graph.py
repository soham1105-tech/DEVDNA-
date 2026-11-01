import json

import pytest

from app.analyzer.python_analyzer import analyse_project
from app.graph.engine import build_graph, compute_impact, compute_metrics


def _tests(impact, prediction):
    return {t["name"] for t in impact["tests"] if t["prediction"] == prediction}


def test_db_failure_propagates_through_startup(sample_graph):
    impact = compute_impact(sample_graph, "db_unavailable", "database:sqlite")
    # 5 routes propagate the fault definitely; the health route handles
    # sqlite3.Error around its check_db call site, so it is only 'possible'
    assert impact["summary"]["routes_definite"] == 5
    assert "app:shoplite.main.app" in {a["id"] for a in impact["affected"] if a["certainty"] == "definite"}
    health = next(a for a in impact["affected"] if a["id"] == "route:GET /health")
    assert health["certainty"] == "possible"
    assert health["handled_at"]["handles"] == ["sqlite3.Error"]
    assert _tests(impact, "pass") == {"test_compute_total_without_discount", "test_discount_codes",
                                      "test_quantity_must_be_positive", "test_worker_delivers_events"}
    # the health route's own test is now may_fail: the route handles sqlite3.Error
    assert len(_tests(impact, "fail")) == 7
    assert _tests(impact, "may_fail") == {"test_health_ok"}


def test_paths_end_at_the_failing_component(sample_graph):
    impact = compute_impact(sample_graph, "db_unavailable", "database:sqlite")
    for a in impact["affected"]:
        assert a["path"][0] == a["id"] and a["path"][-1] == "database:sqlite"
        assert a["distance"] == len(a["path"]) - 1


def test_handled_errors_make_paths_uncertain(sample_graph):
    impact = compute_impact(sample_graph, "worker_unavailable", "worker:shoplite.worker.NotificationWorker")
    assert _tests(impact, "fail") == {"test_worker_delivers_events"}
    assert "test_create_order" in _tests(impact, "may_fail")
    t = next(t for t in impact["tests"] if t["name"] == "test_create_order")
    assert t["handled_at"]["handles"] == ["WorkerUnavailable"]
    # start() is a launcher, not a seed, so app startup is not predicted to fail
    assert "app:shoplite.main.app" not in {a["id"] for a in impact["affected"]}


def test_reraising_handlers_do_not_count_as_guards(sample_graph):
    impact = compute_impact(sample_graph, "external_api_unavailable", "external:SHIPPING_API_URL")
    assert {"test_create_order", "test_shipping_quote", "test_get_order"} <= _tests(impact, "fail")
    assert "test_list_products" in _tests(impact, "pass")


def test_env_var_with_default_is_uncertain(sample_graph):
    impact = compute_impact(sample_graph, "missing_env_var", "env:DATABASE_PATH")
    assert impact["summary"]["definite"] == 0 and impact["summary"]["possible"] > 0


def test_scenario_target_validation(sample_graph):
    with pytest.raises(ValueError, match="needs a database"):
        compute_impact(sample_graph, "db_unavailable", "env:SHIPPING_API_URL")
    with pytest.raises(ValueError, match="not in the analysed graph"):
        compute_impact(sample_graph, "db_unavailable", "database:nope")
    with pytest.raises(ValueError, match="Unsupported"):
        compute_impact(sample_graph, "meteor_strike", "database:sqlite")


def test_metrics_are_deterministic_and_bounded(sample_graph):
    m1, m2 = compute_metrics(sample_graph), compute_metrics(sample_graph)
    assert m1 == m2
    assert all(0 <= v["criticality"] <= 100 for v in m1.values())
    assert m1["func:shoplite.db.get_connection"]["criticality"] > m1["func:shoplite.services.pricing.apply_discount"]["criticality"]
    assert m1["database:sqlite"]["routes_depending"] == 6


# --- engine-correctness regressions: mock matching, purity, path-scoped
# relevance, worker seeding ---

def _worker_run_graph():
    """Graph mirroring ShopLite's worker: a private _run method on the worker
    class (test case for worker seeding)."""
    components = [
        {"id": "worker:shoplite.worker.NotificationWorker", "type": "worker", "name": "NotificationWorker",
         "file": "worker.py", "line": 10, "data": {"module": "shoplite.worker", "cls": "NotificationWorker",
                                                   "launcher_methods": ["start"]}},
        {"id": "func:shoplite.worker.NotificationWorker.start", "type": "function", "name": "start",
         "file": "worker.py", "line": 12, "data": {"module": "shoplite.worker", "cls": "NotificationWorker"}},
        {"id": "func:shoplite.worker.NotificationWorker._run", "type": "function", "name": "_run",
         "file": "worker.py", "line": 30, "data": {"module": "shoplite.worker", "cls": "NotificationWorker",
                                                   "raises": ["WorkerUnavailable"]}},
        {"id": "func:tests.test_worker.test_run_delivers", "type": "test", "name": "test_run_delivers",
         "file": "tests/test_worker.py", "line": 5,
         "data": {"module": "tests.test_worker", "nodeid": "tests/test_worker.py::test_run_delivers",
                  "junit_key": "tests/test_worker.py::test_run_delivers"}},
    ]
    edges = [
        {"id": "calls|func:shoplite.worker.NotificationWorker.start|worker:shoplite.worker.NotificationWorker",
         "source": "func:shoplite.worker.NotificationWorker.start",
         "target": "worker:shoplite.worker.NotificationWorker", "type": "runs_worker",
         "classification": "confirmed", "evidence": [], "call_sites": []},
        {"id": "calls|func:shoplite.worker.NotificationWorker._run|worker:shoplite.worker.NotificationWorker",
         "source": "func:shoplite.worker.NotificationWorker._run",
         "target": "worker:shoplite.worker.NotificationWorker", "type": "runs_worker",
         "classification": "confirmed", "evidence": [], "call_sites": []},
        {"id": "exercises|func:tests.test_worker.test_run_delivers|func:shoplite.worker.NotificationWorker._run",
         "source": "func:tests.test_worker.test_run_delivers",
         "target": "func:shoplite.worker.NotificationWorker._run", "type": "exercises",
         "classification": "confirmed", "evidence": [], "call_sites": []},
    ]
    return build_graph(components, edges)


def test_worker_scenario_seeds_private_run_methods():
    from app.graph.engine import _seeds

    g = _worker_run_graph()
    seeds = _seeds(g, "worker_unavailable", "worker:shoplite.worker.NotificationWorker")
    assert "func:shoplite.worker.NotificationWorker._run" in seeds  # private method, not a launcher
    assert "func:shoplite.worker.NotificationWorker.start" not in seeds  # launcher excluded
    impact = compute_impact(g, "worker_unavailable", "worker:shoplite.worker.NotificationWorker")
    t = next(t for t in impact["tests"] if t["name"] == "test_run_delivers")
    assert t["prediction"] == "fail" and t["certainty"] == "definite"


def test_double_compute_impact_on_same_graph_is_stable(tmp_path, monkeypatch):
    """compute_impact must be pure: no mocked flags written into the caller's
    graph, and two runs on the same graph give identical results."""
    (tmp_path / "client.py").write_text(
        "import httpx\n"
        "def fetch_data():\n    return httpx.get('https://api.example.com/x')\n", encoding="utf-8")
    (tmp_path / "test_client.py").write_text(
        "from client import fetch_data\n"
        "def test_fetch(monkeypatch):\n"
        "    monkeypatch.setattr('client.fetch_data', lambda: 2)\n"
        "    assert fetch_data() == 2\n",
        encoding="utf-8")
    r = analyse_project(tmp_path)
    g = build_graph(r["components"], r["edges"])
    target = next(c["id"] for c in r["components"] if c["type"] == "external_api")
    if target not in g:  # mock graph without an external api: target the function
        target = "func:client.fetch_data"
    # the analyser legitimately marks the mocked edge at analysis time; the
    # graph must not gain NEW mocked flags from compute_impact itself
    before = {(e["id"], bool(e.get("mocked"))) for _, _, e in g.edges(data=True)}
    first = compute_impact(g, "external_api_unavailable", target)
    snapshot = json.dumps(first, sort_keys=True)
    second = compute_impact(g, "external_api_unavailable", target)
    after = {(e["id"], bool(e.get("mocked"))) for _, _, e in g.edges(data=True)}
    assert json.dumps(second, sort_keys=True) == snapshot
    assert before == after  # graph untouched by compute_impact
    assert first["mocked_edges"]  # the mocked first hop was recorded in the result


def test_mock_target_matches_resolved_paths_not_substrings():
    """Mocking `orders` must not match func:...services.orders.create_order
    (substring), but mocking the module path must match its node."""
    components = [
        {"id": "database:sqlite", "type": "database", "name": "sqlite database", "data": {}},
        {"id": "module:services.orders", "type": "module", "name": "services.orders", "data": {}},
        {"id": "func:services.orders.create_order", "type": "function", "name": "create_order",
         "data": {"module": "services.orders"}},
        {"id": "func:test_x.test_create", "type": "test", "name": "test_create",
         "data": {"module": "test_x", "nodeid": "test_x.py::test_create",
                  "junit_key": "test_x.py::test_create", "mocks": ["orders"]}},
        {"id": "func:test_x.test_module_patch", "type": "test", "name": "test_module_patch",
         "data": {"module": "test_x", "nodeid": "test_x.py::test_module_patch",
                  "junit_key": "test_x.py::test_module_patch", "mocks": ["services.orders.create_order"]}},
    ]
    edges = [
        {"id": "calls|func:test_x.test_create|func:services.orders.create_order",
         "source": "func:test_x.test_create", "target": "func:services.orders.create_order",
         "type": "calls", "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 2, "file": "test_x.py", "tries": []}]},
        {"id": "uses_database|func:services.orders.create_order|database:sqlite",
         "source": "func:services.orders.create_order", "target": "database:sqlite",
         "type": "uses_database", "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 3, "file": "services/orders.py", "tries": []}]},
        {"id": "calls|func:test_x.test_module_patch|func:services.orders.create_order",
         "source": "func:test_x.test_module_patch", "target": "func:services.orders.create_order",
         "type": "calls", "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 6, "file": "test_x.py", "tries": []}]},
    ]
    g = build_graph(components, edges)
    impact = compute_impact(g, "db_unavailable", "database:sqlite")
    tests = {t["name"]: t for t in impact["tests"]}
    # 'orders' does NOT resolve to func:...create_order -> no mock flag
    assert "mocked" not in tests["test_create"] and tests["test_create"]["prediction"] == "fail"
    # 'services.orders' resolves to the module node -> mocked
    assert tests["test_module_patch"].get("mocked") is True
    assert tests["test_module_patch"]["prediction"] in ("may_fail", "uncertain")
    assert impact["mocked_edges"]  # recorded, e.g. for the module-patch test


def test_unrelated_valueerror_does_not_demote_definite():
    """A ValueError raised (and handled) on an unrelated branch must not demote
    definite predictions on paths that cannot raise it."""
    components = [
        {"id": "database:sqlite", "type": "database", "name": "sqlite database", "data": {}},
        {"id": "func:mod.cleanup", "type": "function", "name": "cleanup",
         "data": {"module": "mod", "raises": ["ValueError"]}},
        {"id": "func:mod.worker_job", "type": "function", "name": "worker_job", "data": {"module": "mod"}},
        {"id": "func:mod.stats", "type": "function", "name": "stats", "data": {"module": "mod"}},
        {"id": "func:mod.reporter", "type": "function", "name": "reporter", "data": {"module": "mod"}},
        {"id": "func:mod.cautious", "type": "function", "name": "cautious", "data": {"module": "mod"}},
    ]
    edges = [
        {"id": "uses_database|func:mod.cleanup|database:sqlite",
         "source": "func:mod.cleanup", "target": "database:sqlite", "type": "uses_database",
         "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 3, "file": "mod.py", "tries": []}]},
        {"id": "calls|func:mod.worker_job|func:mod.cleanup",
         "source": "func:mod.worker_job", "target": "func:mod.cleanup", "type": "calls",
         "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 8, "file": "mod.py", "tries": []}]},  # unguarded
        {"id": "uses_database|func:mod.stats|database:sqlite",
         "source": "func:mod.stats", "target": "database:sqlite", "type": "uses_database",
         "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 12, "file": "mod.py", "tries": []}]},
        {"id": "calls|func:mod.reporter|func:mod.stats",
         "source": "func:mod.reporter", "target": "func:mod.stats", "type": "calls",
         "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 16, "file": "mod.py",
                         "tries": [[{"names": ["ValueError"], "reraises": False, "line": 15}]]}]},
        {"id": "calls|func:mod.cautious|func:mod.stats",
         "source": "func:mod.cautious", "target": "func:mod.stats", "type": "calls",
         "classification": "confirmed", "evidence": [],
         "call_sites": [{"line": 20, "file": "mod.py",
                         "tries": [[{"names": ["sqlite3.Error"], "reraises": False, "line": 19}]]}]},
    ]
    g = build_graph(components, edges)
    impact = compute_impact(g, "db_unavailable", "database:sqlite")
    certainty = {a["id"]: a["certainty"] for a in impact["affected"]}
    # unguarded path -> definite
    assert certainty["func:mod.worker_job"] == "definite"
    # the reporter's ValueError handler guards a path that cannot raise
    # ValueError -> must NOT demote it (the old globally-pooled raised set did)
    assert certainty["func:mod.reporter"] == "definite"
    # a handler catching a fault-relevant exception still demotes
    assert certainty["func:mod.cautious"] == "possible"
