"""Module 6 — PR intelligence: deterministic review package composition."""
import pytest

from app import db as dbmod
from app.graph.engine import build_graph
from app.services.pr_review import _findings_on_files, pr_review


@pytest.fixture()
def db_session():
    db = dbmod.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _comp(cid, ctype, name, file, data=None):
    return {"id": cid, "type": ctype, "name": name, "file": file, "line": 1, "data": data or {}}


def _edge(eid, src, dst, etype):
    return {"id": eid, "source": src, "target": dst, "type": etype,
            "classification": "confirmed", "evidence": [], "call_sites": []}


def _graph():
    comps = [
        _comp("test:tests.test_b", "test", "test_b", "tests/test_b.py",
              {"nodeid": "tests/test_b.py::test_b"}),
        _comp("module:app.a", "module", "app.a", "app/a.py"),
        _comp("module:app.b", "module", "app.b", "app/b.py"),
        _comp("module:app.c", "module", "app.c", "app/c.py"),
    ]
    edges = [
        _edge("e1", "test:tests.test_b", "module:app.b", "exercises"),
        _edge("e2", "module:app.a", "module:app.b", "calls"),
    ]
    return build_graph(comps, edges)


def _findings():
    return [
        {"id": "f1", "rule": "hardcoded_secret", "title": "secret in a", "severity": "high",
         "component_id": None, "evidence": [{"file": "app/a.py", "line": 3}],
         "confidence": "high", "review_status": "open"},
        {"id": "f2", "rule": "untested_route", "title": "route untested", "severity": "low",
         "component_id": None, "evidence": [{"file": "app/z.py", "line": 9}],
         "confidence": "medium", "review_status": "open"},
    ]


def test_package_composes_all_sections(db_session):
    res = pr_review(db_session, _graph(), ["app/a.py", "app/b.py"], _findings(), project_id="p-pr")
    # b is a seed; a is a seed too, so affected is empty but the test that
    # reaches b is flagged
    assert res["impact"]["summary"]["files_mapped"] == 2
    assert res["impact"]["tests_may_break"][0]["prediction"] == "may_break"
    cov = {c["file"]: c for c in res["test_coverage"]}
    assert cov["app/b.py"]["status"] == "covered"
    assert cov["app/a.py"]["status"] == "uncovered"  # no test reaches a
    assert res["findings"][0]["id"] == "f1"  # finding on app/a.py matches
    assert res["reliability"]["status"] == "empty"  # no experiments recorded
    assert res["summary"]["tests_may_break"] == 1
    assert res["summary"]["changed_files_without_test_coverage"] == 1
    assert res["next_steps"] and res["limitations"]
    assert "not an approval" in " ".join(res["next_steps"] + res["limitations"])


def test_uncovered_changed_file_produces_next_step(db_session):
    res = pr_review(db_session, _graph(), ["app/c.py"], _findings(), project_id="p-pr")
    cov = {c["file"]: c for c in res["test_coverage"]}
    assert cov["app/c.py"]["status"] == "uncovered"
    assert res["summary"]["changed_files_without_test_coverage"] == 1
    assert any("Add or extend tests" in s for s in res["next_steps"])


def test_findings_matching_uses_normalised_paths(db_session):
    assert _findings_on_files(_findings(), {"a/app/a.py"})[0]["id"] == "f1"
    assert _findings_on_files(_findings(), {"app\\a.py"})[0]["id"] == "f1"
    assert _findings_on_files(_findings(), {"other.py"}) == []


def test_empty_reliability_is_reported_not_fabricated(db_session):
    res = pr_review(db_session, _graph(), ["app/b.py"], [], project_id="p-pr-none")
    assert res["reliability"]["status"] == "empty"
    assert res["reliability"]["observations"] == 0
    assert "Run controlled experiments" in res["reliability"]["note"]


# --- API --------------------------------------------------------------------


def test_api_pr_review_on_sample(client, sample_project):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Component

    db = SessionLocal()
    try:
        target_file = db.scalars(
            select(Component.file).where(Component.type == "module").limit(1)).first()
    finally:
        db.close()
    r = client.post(f"/api/projects/{sample_project}/pr-review",
                    json={"files": [target_file], "title": "Demo PR"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Demo PR"
    assert body["summary"]["files_mapped"] == 1
    assert body["test_coverage"] and body["next_steps"] and body["limitations"]
    assert body["project"]["id"] == sample_project


def test_api_pr_review_validates_input(client, sample_project):
    assert client.post(f"/api/projects/{sample_project}/pr-review",
                       json={"files": []}).status_code == 422


def test_api_pr_review_unknown_project(client):
    assert client.post("/api/projects/nope/pr-review",
                       json={"files": ["x.py"]}).status_code == 404
