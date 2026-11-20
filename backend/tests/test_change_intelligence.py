"""Tests for DevDNA Change Intelligence platform layer.

Covers:
- Git diff parsing (added, modified, deleted, renamed files)
- AST-level changed function/class/import detection
- Before-and-after dependency graph comparison
- Explainable Change Risk Score calculation and factor weights
- 5-Tiered Intelligent Test Prioritisation
- Controlled validation experiment integration
- Evidence-backed merge recommendations (APPROVE, REVIEW REQUIRED, BLOCK RECOMMENDED)
- GitHub webhook signature verification and Check Run payload generation
- API endpoints: /analyze, list, get, and Buildathon demo scenario
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from app.services.change_detection import (
    FileChange,
    build_change_manifest,
    enrich_file_changes_with_ast,
    parse_unified_diff,
)
from app.services.change_risk import calculate_change_risk
from app.services.github_webhook import format_github_check_run, render_change_intelligence_markdown, verify_github_signature
from app.services.graph_diff import diff_dependency_graphs
from app.services.merge_recommendation import generate_merge_recommendation
from app.services.test_prioritization import prioritize_tests

SAMPLE_DIFF = """diff --git a/shoplite/db.py b/shoplite/db.py
index 1234567..89abcdef 100644
--- a/shoplite/db.py
+++ b/shoplite/db.py
@@ -14,6 +14,8 @@
 def get_db():
-    db = SessionLocal()
+    # Modified database connection pool handler
+    db = SessionLocal(expire_on_commit=False, isolation_level="SERIALIZABLE")
     try:
         yield db
     finally:
diff --git a/shoplite/new_service.py b/shoplite/new_service.py
new file mode 100644
--- /dev/null
+++ b/shoplite/new_service.py
@@ -0,0 +1,5 @@
+def helper():
+    return True
diff --git a/shoplite/old_service.py b/shoplite/old_service.py
deleted file mode 100644
--- a/shoplite/old_service.py
+++ /dev/null
@@ -1,5 +0,0 @@
-def legacy():
-    pass
diff --git a/shoplite/legacy_util.py b/shoplite/renamed_util.py
similarity index 100%
rename from shoplite/legacy_util.py
rename to shoplite/renamed_util.py
"""


def test_parse_unified_diff_extracts_all_file_types():
    changes = parse_unified_diff(SAMPLE_DIFF)
    assert len(changes) == 4

    by_path = {c.path: c for c in changes}
    assert "shoplite/db.py" in by_path
    assert by_path["shoplite/db.py"].status == "modified"
    assert by_path["shoplite/db.py"].changed_lines == [(14, 21)]

    assert "shoplite/new_service.py" in by_path
    assert by_path["shoplite/new_service.py"].status == "added"

    assert "shoplite/old_service.py" in by_path
    assert by_path["shoplite/old_service.py"].status == "deleted"

    assert "shoplite/renamed_util.py" in by_path
    assert by_path["shoplite/renamed_util.py"].status == "renamed"
    assert by_path["shoplite/renamed_util.py"].old_path == "shoplite/legacy_util.py"


def test_enrich_file_changes_with_ast(tmp_path):
    base_repo = tmp_path / "base"
    base_repo.mkdir()
    (base_repo / "service.py").write_text(
        "import os\n\ndef process(data):\n    return data * 2\n\ndef untouched():\n    return 0\n",
        encoding="utf-8",
    )

    mod_repo = tmp_path / "mod"
    mod_repo.mkdir()
    (mod_repo / "service.py").write_text(
        "import os\nimport sys\n\ndef process(data):\n    # changed logic\n    return data * 3\n\ndef untouched():\n    return 0\n",
        encoding="utf-8",
    )

    change = FileChange(path="service.py", status="modified", changed_lines=[(6, 7)])
    enrich_file_changes_with_ast([change], base_root=base_repo, head_root=mod_repo)

    assert change.granularity == "function_level"
    comp_names = [c["name"] for c in change.changed_components]
    assert "process" in comp_names
    # Untouched function should not be in changed_components
    assert "untouched" not in comp_names

    # Changed imports should include 'sys'
    imp_names = [i["name"] for i in change.changed_imports if i["change_type"] == "added"]
    assert "sys" in imp_names


def test_diff_dependency_graphs_and_propagation():
    base_g = nx.DiGraph()
    base_g.add_node("func:db:get_db", type="function", name="get_db", file="db.py", line=10)
    base_g.add_node("func:orders:create", type="function", name="create", file="orders.py", line=20)
    base_g.add_node("route:POST /orders", type="route", name="POST /orders", file="api.py", line=30, data={"verb": "POST"})
    base_g.add_node("test:test_orders:test_create", type="test", name="test_create", file="test_orders.py", line=40, data={"nodeid": "test_orders.py::test_create"})

    # Edges: orders.create calls db.get_db; route POST /orders calls orders.create; test exercises orders.create
    base_g.add_edge("func:orders:create", "func:db:get_db", type="calls", classification="confirmed")
    base_g.add_edge("route:POST /orders", "func:orders:create", type="calls", classification="confirmed")
    base_g.add_edge("test:test_orders:test_create", "func:orders:create", type="exercises", classification="confirmed")

    mod_g = base_g.copy()
    # Add a new dependency: orders calls cache
    mod_g.add_node("func:cache:get", type="function", name="get", file="cache.py", line=5)
    mod_g.add_edge("func:orders:create", "func:cache:get", type="calls", classification="confirmed")

    diff = diff_dependency_graphs(
        base_graph=base_g,
        mod_graph=mod_g,
        changed_components=[{"id": "func:db:get_db", "name": "get_db", "type": "function", "change_type": "modified"}],
        changed_files=["db.py"],
    )

    summary = diff["summary"]
    assert summary["directly_affected_count"] == 1
    assert summary["transitively_affected_count"] == 1  # orders:create
    assert summary["impacted_routes_count"] == 1        # POST /orders
    assert summary["associated_tests_count"] == 1       # test_create
    assert summary["new_dependencies_count"] == 1       # orders -> cache

    # Check impacted route
    assert diff["impacted_routes"][0]["route"] == "POST /orders"


def test_change_risk_score_calculation(sample_project):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        mock_diff = {
            "summary": {
                "total_nodes_base": 10,
                "total_nodes_modified": 10,
                "directly_affected_count": 1,
                "transitively_affected_count": 3,
                "potentially_affected_count": 0,
                "impacted_routes_count": 2,
                "associated_tests_count": 2,
            },
            "directly_affected": [{"id": "db:get_db", "name": "get_db", "distance": 0, "is_critical": True}],
            "transitively_affected": [{"id": "orders:create", "name": "create", "distance": 1}],
            "potentially_affected": [],
            "critical_components_affected": [{"id": "db:get_db", "name": "get_db", "is_critical": True}],
            "impacted_routes": [{"id": "r1", "route": "POST /orders", "distance": 2}, {"id": "r2", "route": "GET /orders", "distance": 2}],
            "associated_tests": [{"id": "t1", "name": "test_create", "distance": 1}],
        }
        risk = calculate_change_risk(db, sample_project, mock_diff, findings=[], unmapped_files=[])
        assert 0 <= risk["score"] <= 100
        assert risk["level"] in ("low", "medium", "high")
        assert len(risk["factors"]) == 6
        weights = sum(f["weight"] for f in risk["factors"])
        assert round(weights, 2) == 1.0
    finally:
        db.close()


def test_test_prioritization_tiers():
    g = nx.DiGraph()
    g.add_node("func:db", type="function", name="db_fn")
    g.add_node("func:svc", type="function", name="svc_fn")
    g.add_node("route:GET /items", type="route", name="GET /items", data={"verb": "GET"})

    g.add_edge("func:svc", "func:db", type="calls")
    g.add_edge("route:GET /items", "func:svc", type="calls")

    # Tier 1 test
    g.add_node("test:t1", type="test", name="test_db", data={"nodeid": "tests.py::test_db"})
    g.add_edge("test:t1", "func:db", type="exercises")

    # Tier 2 test
    g.add_node("test:t2", type="test", name="test_svc", data={"nodeid": "tests.py::test_svc"})
    g.add_edge("test:t2", "func:svc", type="exercises")

    # Tier 4 test
    g.add_node("test:t4", type="test", name="test_route", data={"nodeid": "tests.py::test_route"})
    g.add_edge("test:t4", "route:GET /items", type="exercises")

    mock_diff = {
        "directly_affected": [{"id": "func:db"}],
        "transitively_affected": [{"id": "func:svc"}],
        "impacted_routes": [{"id": "route:GET /items"}],
        "associated_tests": [],
    }

    res = prioritize_tests(g, mock_diff)
    tests = res["tests"]
    assert len(tests) >= 3

    tier1 = next(t for t in tests if t["test_id"] == "test:t1")
    assert tier1["tier"] == 1
    assert "Directly exercises modified component" in tier1["selection_reason"]

    tier2 = next(t for t in tests if t["test_id"] == "test:t2")
    assert tier2["tier"] == 2

    tier4 = next(t for t in tests if t["test_id"] == "test:t4")
    assert tier4["tier"] == 4


def test_generate_merge_recommendation_branches():
    mock_diff = {
        "summary": {"directly_affected_count": 1, "transitively_affected_count": 2},
        "directly_affected": [{"id": "c1"}],
        "transitively_affected": [{"id": "c2"}],
        "critical_components_affected": [{"id": "c1"}],
        "impacted_routes": [{"route": "GET /api"}],
    }
    mock_tests = {"summary": {"total_prioritized_tests": 3, "tier_1_direct": 1}}

    # Case 1: High risk with high finding -> BLOCK RECOMMENDED
    rec_block = generate_merge_recommendation(
        risk={"score": 85, "level": "high"},
        graph_diff=mock_diff,
        prioritized_tests=mock_tests,
        findings=[{"severity": "high", "title": "SQL Injection vulnerability"}],
    )
    assert rec_block["decision"] == "BLOCK RECOMMENDED"
    assert rec_block["blockers"]

    # Case 2: Moderate risk -> REVIEW REQUIRED
    rec_review = generate_merge_recommendation(
        risk={"score": 50, "level": "medium"},
        graph_diff=mock_diff,
        prioritized_tests=mock_tests,
        findings=[],
    )
    assert rec_review["decision"] == "REVIEW REQUIRED"

    # Case 3: Low risk without routes or findings -> APPROVE
    low_diff = {"summary": {"directly_affected_count": 1}, "directly_affected": [{"id": "c1"}], "transitively_affected": [], "critical_components_affected": [], "impacted_routes": []}
    rec_approve = generate_merge_recommendation(
        risk={"score": 20, "level": "low"},
        graph_diff=low_diff,
        prioritized_tests=mock_tests,
        findings=[],
    )
    assert rec_approve["decision"] == "APPROVE"


def test_github_webhook_signature_verification():
    secret = "test-webhook-secret-xyz"
    payload = b'{"action":"opened","pull_request":{"number":42}}'

    # Compute valid signature
    import hashlib
    import hmac
    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    valid_header = f"sha256={mac.hexdigest()}"

    assert verify_github_signature(payload, secret, valid_header) is True
    assert verify_github_signature(payload, secret, "sha256=invalidhexdigest12345") is False
    assert verify_github_signature(payload, "wrong-secret", valid_header) is False
    assert verify_github_signature(payload, secret, None) is False


def test_format_github_check_run_and_markdown():
    mock_report = {
        "recommendation": {
            "decision": "BLOCK RECOMMENDED",
            "headline": "High-risk changes with confirmed failure propagation.",
            "impacted_components_count": 5,
            "impacted_routes_count": 2,
            "prioritized_tests_count": 4,
            "recommended_actions": ["Add fallback handler", "Fix session leak"],
        },
        "risk_assessment": {
            "score": 82,
            "level": "high",
            "factors": [
                {"name": "Blast Radius", "weight": 0.25, "raw_score": 80, "reason": "5 components affected"},
            ],
        },
        "graph_diff": {
            "summary": {"directly_affected_count": 1, "transitively_affected_count": 4},
            "impacted_routes": [{"route": "POST /orders", "distance": 1}],
        },
        "prioritized_tests": {
            "tests": [
                {"tier": 1, "tier_name": "Direct Component Test", "name": "test_db", "selection_reason": "Direct exercise", "coverage_confidence": "high"},
            ],
        },
        "validation": {
            "executed": True,
            "fault": {"kind": "db_unavailable", "target": "database connection pool"},
            "baseline_summary": {"passed": 5},
            "faulted_summary": {"passed": 2, "failed": 3},
            "accuracy": 1.0,
        },
        "ai_explanation": {
            "summary": "Database session handler modification breaks orders route.",
            "propagation_narrative": "get_db -> pricing -> orders -> POST /orders.",
        },
    }

    check = format_github_check_run(mock_report)
    assert check["conclusion"] == "failure"
    assert "BLOCK RECOMMENDED" in check["output"]["title"]

    md = render_change_intelligence_markdown(mock_report)
    assert "DevDNA Change Intelligence: `BLOCK RECOMMENDED`" in md
    assert "POST /orders" in md
    assert "db_unavailable" in md
    assert "test_db" in md


def test_api_change_intelligence_endpoints(client, sample_project):
    # 1. POST /api/projects/{id}/change-intelligence/analyze with files
    r = client.post(
        f"/api/projects/{sample_project}/change-intelligence/analyze",
        json={"files": ["shoplite/db.py"], "title": "Test Change Analysis"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"]
    assert data["project_id"] == sample_project
    assert data["status"] == "completed"
    assert "risk_assessment" in data
    assert "recommendation" in data
    assert "graph_diff" in data
    assert "prioritized_tests" in data
    analysis_id = data["id"]

    # 2. GET /api/projects/{id}/change-intelligence
    list_res = client.get(f"/api/projects/{sample_project}/change-intelligence")
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(item["id"] == analysis_id for item in items)

    # 3. GET /api/projects/{id}/change-intelligence/{analysis_id}
    detail_res = client.get(f"/api/projects/{sample_project}/change-intelligence/{analysis_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == analysis_id

    # 4. POST /api/projects/{id}/change-intelligence/{analysis_id}/validate
    val_res = client.post(f"/api/projects/{sample_project}/change-intelligence/{analysis_id}/validate")
    assert val_res.status_code == 200
    val_data = val_res.json()
    assert val_data["validation"] is not None
    assert val_data["validation"]["executed"] is True
    assert val_data["validation"]["fault"]["kind"] == "db_unavailable"


def test_api_change_intelligence_demo_scenario(client, sample_project):
    """Verify Buildathon demo scenario runs end-to-end and executes validation experiment."""
    r = client.post(f"/api/projects/{sample_project}/change-intelligence/demo")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "completed"
    assert "Demo PR #42" in body["title"]

    # Diff manifest
    files = body["diff_manifest"]["files"]
    assert any(f["path"] == "shoplite/db.py" for f in files)

    # Risk score
    assert body["risk_assessment"]["score"] > 0
    assert len(body["risk_assessment"]["factors"]) == 6

    # Prioritized tests
    tests = body["prioritized_tests"]["tests"]
    assert len(tests) > 0
    assert any(t["tier"] in (1, 2, 4) for t in tests)

    # Validation
    assert body["validation"] is not None
    assert body["validation"]["executed"] is True
    assert body["validation"]["fault"]["kind"] == "db_unavailable"
    assert body["validation"]["faulted_summary"]["fault_triggered"] is True

    # Recommendation
    assert body["recommendation"]["decision"] in ("BLOCK RECOMMENDED", "REVIEW REQUIRED")
    assert len(body["recommendation"]["recommended_actions"]) > 0

    # Grounded explanation
    assert body["ai_explanation"] is not None
    assert body["ai_explanation"]["summary"]
    assert body["ai_explanation"]["propagation_narrative"]
