"""Tests for JavaScript (Jest) empirical verification."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.analyzer.js_analyzer import analyse_js_project
from app.experiments import runner
from app.experiments.runner import choose_test_runner, compare
from app.graph.engine import build_graph, compute_impact

JS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "js_shop"
JEST_AVAILABLE = (JS_FIXTURE / "node_modules" / "jest" / "package.json").is_file()

pytestmark = pytest.mark.skipif(
    not JEST_AVAILABLE,
    reason="Jest dependencies are not installed; run npm install in backend/tests/fixtures/js_shop",
)


@pytest.fixture(scope="module")
def js_graph():
    analysis = analyse_js_project(JS_FIXTURE, use_cache=False)
    return build_graph(analysis["components"], analysis["edges"])


def _dynamic_impact(graph, target_id: str) -> dict:
    return compute_impact(
        graph,
        "changed_component",
        target_id,
        custom_spec={
            "label": "Potential JS failure",
            "fault": "A failure introduced in the JS component.",
            "exceptions": ["Error"],
            "target_types": [],
        },
    )


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_jest_baseline_passes():
    observation = runner.execute_js(JS_FIXTURE, None, "local_harness")
    assert observation["baseline"]["counts"] == {"passed": 3, "failed": 0, "error": 0, "skipped": 0}
    assert observation["baseline"]["exit_code"] == 0
    assert set(observation["baseline"]["results"]) == {
        "tests.api.test::GET /api/products returns the catalog",
        "tests.api.test::GET /api/products/:id returns one product",
        "tests.api.test::POST /api/orders creates an order",
    }


def test_dynamic_fault_in_order_service_fails_predicted_order_test(js_graph, monkeypatch):
    monkeypatch.setattr(runner, "MIN_DEFINITE_TO_GRADE", 0)
    impact = _dynamic_impact(js_graph, "func:services.orders.createOrder")
    assert any(t["prediction"] == "fail" for t in impact["tests"] if t["name"].startswith("POST /api/orders"))

    observation = runner.execute(JS_FIXTURE, runner.fault_spec(impact), "local_harness", runner="jest")
    assert observation["baseline"]["counts"]["passed"] == 3
    assert observation["faulted"]["fault_state"]["triggers"] > 0

    comparison = compare(impact, observation)
    rows = {row["test"]: row for row in comparison["rows"]}
    assert rows["POST /api/orders creates an order"]["observed"] == "failed"
    assert rows["POST /api/orders creates an order"]["verdict"] == "match"
    assert rows["GET /api/products returns the catalog"]["observed"] == "passed"
    assert rows["GET /api/products/:id returns one product"]["observed"] == "passed"
    assert comparison["status"] == "prediction_confirmed", comparison


def test_dynamic_fault_in_product_service_fails_predicted_product_test(js_graph, monkeypatch):
    monkeypatch.setattr(runner, "MIN_DEFINITE_TO_GRADE", 0)
    impact = _dynamic_impact(js_graph, "func:services.catalog.listProducts")
    observation = runner.execute_js(JS_FIXTURE, runner.fault_spec(impact), "local_harness")

    comparison = compare(impact, observation)
    rows = {row["test"]: row for row in comparison["rows"]}
    assert rows["GET /api/products returns the catalog"]["observed"] == "failed"
    assert rows["GET /api/products returns the catalog"]["verdict"] == "match"
    assert rows["POST /api/orders creates an order"]["observed"] == "passed"
    assert comparison["status"] == "prediction_confirmed", comparison


def test_external_api_fault_is_injected():
    result = analyse_js_project(JS_FIXTURE, use_cache=False)
    graph = build_graph(result["components"], result["edges"])
    impact = compute_impact(graph, "external_api_unavailable", "external:SHIPPING_API_URL")
    observation = runner.execute_js(JS_FIXTURE, runner.fault_spec(impact), "local_harness")

    assert observation["baseline"]["counts"]["passed"] == 3
    assert observation["faulted"]["fault_state"]["triggers"] > 0
    assert any("fetch" in applied for applied in observation["faulted"]["fault_state"]["applied"])


def test_unknown_runner_returns_blocked(tmp_path):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    runner_name, reason = choose_test_runner(tmp_path)
    assert runner_name == "blocked"
    assert "Vitest" in reason

    package.write_text(json.dumps({"scripts": {"test": "./scripts/test.sh"}}), encoding="utf-8")
    runner_name, reason = choose_test_runner(tmp_path)
    assert runner_name == "blocked"
    assert "does not invoke Jest" in reason

    package.write_text(json.dumps({"scripts": {"test": "jest --ci"}}), encoding="utf-8")
    assert choose_test_runner(tmp_path) == ("jest", None)


def test_js_only_upload_is_accepted(client):
    archive = _zip({
        "package.json": json.dumps({
            "name": "js-only-upload",
            "version": "1.0.0",
            "scripts": {"test": "jest"},
            "dependencies": {"express": "^4.19.0"},
        }),
        "app.js": (
            "import express from 'express';\n"
            "const app = express();\n"
            "app.get('/ping', (req, res) => res.json({ ok: true }));\n"
            "export default app;\n"
        ),
    })
    response = client.post(
        "/api/projects/upload",
        files={"file": ("js-only-upload.zip", archive, "application/zip")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    project = body["project"]
    assert project["ingest_stats"]["languages"].get("JavaScript", 0) >= 1
    assert "no_python" not in response.text

    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["latest_run"]["status"] == "completed"
