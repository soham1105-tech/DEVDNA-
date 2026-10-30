import sqlite3
from pathlib import Path
import pytest

from app.analyzer.python_analyzer import analyse_project
from app.graph.engine import build_graph, compute_impact
from app.experiments.runner import compare


def _edge(res, etype, src, tgt):
    for e in res["edges"]:
        if e["type"] == etype and e["source"] == src and e["target"] == tgt:
            return e
    return None


def test_nested_functions_and_classes(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "def compute(x):\n"
        "    def helper(y):\n"
        "        return y * 2\n"
        "    return helper(x)\n\n"
        "def make_processor():\n"
        "    class LocalProcessor:\n"
        "        def process(self, v):\n"
        "            return v + 1\n"
        "    p = LocalProcessor()\n"
        "    return p.process(10)\n\n"
        "class Service:\n"
        "    class Config:\n"
        "        def get_val(self):\n"
        "            return 42\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)
    comp_ids = {c["id"] for c in r["components"]}

    # Verify nested function component and calls edge
    assert "func:app.compute.<locals>.helper" in comp_ids
    edge_helper = _edge(r, "calls", "func:app.compute", "func:app.compute.<locals>.helper")
    assert edge_helper is not None
    assert edge_helper["classification"] == "confirmed"

    # Verify nested class inside function and method call
    assert "class:app.make_processor.<locals>.LocalProcessor" in comp_ids
    assert "func:app.make_processor.<locals>.LocalProcessor.process" in comp_ids
    edge_proc = _edge(r, "calls", "func:app.make_processor", "func:app.make_processor.<locals>.LocalProcessor.process")
    assert edge_proc is not None

    # Verify nested class inside class
    assert "class:app.Service.Config" in comp_ids
    assert "func:app.Service.Config.get_val" in comp_ids


def test_function_local_imports(tmp_path: Path):
    (tmp_path / "lib.py").write_text(
        "def special_calc():\n"
        "    return 123\n",
        encoding="utf-8"
    )
    (tmp_path / "service.py").write_text(
        "def run():\n"
        "    from lib import special_calc\n"
        "    return special_calc()\n\n"
        "def read_conf():\n"
        "    import os\n"
        "    return os.getenv('LOCAL_CONF')\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)
    
    # Check that function-local import resolves the function call
    edge_call = _edge(r, "calls", "func:service.run", "func:lib.special_calc")
    assert edge_call is not None
    assert edge_call["classification"] == "confirmed"

    # Check module import edge
    edge_import = _edge(r, "imports", "module:service", "module:lib")
    assert edge_import is not None

    # Check env read from function-local os import
    edge_env = _edge(r, "reads_env", "func:service.read_conf", "env:LOCAL_CONF")
    assert edge_env is not None


def test_async_routes_and_functions(tmp_path: Path):
    (tmp_path / "web.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n\n"
        "async def fetch_remote():\n"
        "    return {'status': 'ok'}\n\n"
        "@app.get('/async-data')\n"
        "async def async_handler():\n"
        "    return await fetch_remote()\n",
        encoding="utf-8"
    )
    (tmp_path / "test_web.py").write_text(
        "import pytest\n"
        "from httpx import AsyncClient\n"
        "from web import app\n\n"
        "async def test_async_endpoint():\n"
        "    async with AsyncClient(app=app, base_url='http://test') as ac:\n"
        "        res = await ac.get('/async-data')\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)
    
    # Check route discovery with is_async=True
    route = next((c for c in r["components"] if c["id"] == "route:GET /async-data"), None)
    assert route is not None
    assert route["data"]["is_async"] is True
    assert route["data"]["handler"] == "func:web.async_handler"

    # Check calls edge from async handler to fetch_remote
    edge_call = _edge(r, "calls", "func:web.async_handler", "func:web.fetch_remote")
    assert edge_call is not None
    assert edge_call["classification"] == "confirmed"

    # Check test client exercises edge
    edge_test = _edge(r, "exercises", "test:test_web.py::test_async_endpoint", "route:GET /async-data")
    assert edge_test is not None


def test_django_and_drf_routes(tmp_path: Path):
    (tmp_path / "views.py").write_text(
        "from rest_framework.viewsets import ViewSet\n"
        "from rest_framework.decorators import action\n\n"
        "def my_view(request):\n"
        "    return None\n\n"
        "class ItemViewSet(ViewSet):\n"
        "    def list(self, request):\n"
        "        return []\n"
        "    def create(self, request):\n"
        "        return {}\n"
        "    @action(detail=False, methods=['get'])\n"
        "    def recent(self, request):\n"
        "        return []\n",
        encoding="utf-8"
    )
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\n"
        "from rest_framework.routers import DefaultRouter\n"
        "from views import my_view, ItemViewSet\n\n"
        "router = DefaultRouter()\n"
        "router.register(r'items', ItemViewSet, basename='item')\n\n"
        "urlpatterns = [\n"
        "    path('simple/', my_view, name='simple'),\n"
        "]\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)
    route_ids = {c["id"] for c in r["components"] if c["type"] == "route"}

    assert "Django" in r["frameworks"]
    assert "route:GET /simple" in route_ids
    assert "route:GET /items" in route_ids
    assert "route:POST /items" in route_ids
    assert "route:GET /items/recent" in route_ids

    # Verify handled_by edges
    e_simple = _edge(r, "handled_by", "route:GET /simple", "func:views.my_view")
    assert e_simple is not None and e_simple["classification"] == "confirmed"

    e_list = _edge(r, "handled_by", "route:GET /items", "func:views.ItemViewSet.list")
    assert e_list is not None and e_list["classification"] == "confirmed"

    e_recent = _edge(r, "handled_by", "route:GET /items/recent", "func:views.ItemViewSet.recent")
    assert e_recent is not None and e_recent["classification"] == "confirmed"


def test_import_time_db_connections(tmp_path: Path):
    (tmp_path / "database.py").write_text(
        "import sqlite3\n"
        "db = sqlite3.connect('database.db')\n",
        encoding="utf-8"
    )
    (tmp_path / "repo.py").write_text(
        "from database import db\n"
        "def query_users():\n"
        "    return db.execute('SELECT * FROM users')\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)

    # Database component exists
    assert any(c["id"] == "database:sqlite" for c in r["components"])

    # Import-time DB connection produces module-level uses_database edge
    mod_edge = _edge(r, "uses_database", "module:database", "database:sqlite")
    assert mod_edge is not None
    assert mod_edge["classification"] == "confirmed"
    assert "import time" in mod_edge["evidence"][0].get("detail", "")

    # Calling execute on module-level db also links to database
    repo_edge = _edge(r, "uses_database", "func:repo.query_users", "database:sqlite")
    assert repo_edge is not None


def test_mock_detection_and_uncertain_prediction(tmp_path: Path):
    (tmp_path / "client.py").write_text(
        "import requests\n"
        "def fetch_data():\n"
        "    return requests.get('https://api.example.com/data').json()\n",
        encoding="utf-8"
    )
    (tmp_path / "test_client.py").write_text(
        "from unittest.mock import patch, MagicMock\n"
        "import pytest\n"
        "from client import fetch_data\n\n"
        "@patch('client.requests.get')\n"
        "def test_fetch_with_patch(mock_get):\n"
        "    mock_get.return_value.json.return_value = {'ok': True}\n"
        "    assert fetch_data() == {'ok': True}\n\n"
        "def test_fetch_with_monkeypatch(monkeypatch):\n"
        "    mock = MagicMock()\n"
        "    mock.json.return_value = {'ok': True}\n"
        "    monkeypatch.setattr('client.requests.get', lambda url: mock)\n"
        "    assert fetch_data() == {'ok': True}\n",
        encoding="utf-8"
    )
    r = analyse_project(tmp_path)

    # 1. Check test component mock detection
    t_patch = next(c for c in r["components"] if c["name"] == "test_fetch_with_patch")
    t_monkey = next(c for c in r["components"] if c["name"] == "test_fetch_with_monkeypatch")
    assert t_patch["data"]["mocked"] is True
    assert t_monkey["data"]["mocked"] is True

    # 2. Check edges are marked mocked
    e_patch = _edge(r, "calls", t_patch["id"], "func:client.fetch_data")
    assert e_patch is not None and e_patch.get("mocked") is True

    e_monkey = _edge(r, "calls", t_monkey["id"], "func:client.fetch_data")
    assert e_monkey is not None and e_monkey.get("mocked") is True

    # 3. Compute impact: all covering tests mock the dependency -> labelled uncertain, NOT confirmed
    graph = build_graph(r["components"], r["edges"])
    impact = compute_impact(graph, "external_api_unavailable", "external:api.example.com")

    assert impact["prediction"] == "uncertain — covering tests mock the dependency"
    for t in impact["tests"]:
        assert t["prediction"] == "uncertain"
        assert t["handled_at"]["mocked"] is True

    # 4. In experiment compare: result is labelled uncertain, not confirmed
    observation = {
        "baseline": {"timed_out": False, "results": {
            "test_client::test_fetch_with_patch": {"outcome": "passed", "message": "", "time": 0.01},
            "test_client::test_fetch_with_monkeypatch": {"outcome": "passed", "message": "", "time": 0.01},
        }, "fault_state": {}},
        "faulted": {"timed_out": False, "results": {
            "test_client::test_fetch_with_patch": {"outcome": "passed", "message": "", "time": 0.01},
            "test_client::test_fetch_with_monkeypatch": {"outcome": "passed", "message": "", "time": 0.01},
        }, "fault_state": {"triggers": 1, "errors": []}}
    }
    c = compare(impact, observation)
    assert c["status"] == "uncertain — covering tests mock the dependency"
    assert c["status"] != "prediction_confirmed"
    assert c["status"] != "prediction_contradicted"
