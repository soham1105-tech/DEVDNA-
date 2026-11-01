"""Module 2 — change-impact hardening: changed-files impact predictions.

Service tests use a small synthetic graph so edge semantics are explicit;
API tests run against the analysed sample project.
"""
from app.graph.engine import build_graph
from app.services.change_impact import changed_files_impact, normalise_path


def _comp(cid, ctype, name, file, data=None):
    return {"id": cid, "type": ctype, "name": name, "file": file, "line": 1, "data": data or {}}


def _edge(eid, src, dst, etype):
    return {"id": eid, "source": src, "target": dst, "type": etype,
            "classification": "confirmed", "evidence": [], "call_sites": []}


def _graph():
    comps = [
        _comp("module:app.a", "module", "app.a", "app/a.py"),
        _comp("module:app.b", "module", "app.b", "app/b.py"),
        _comp("module:app.c", "module", "app.c", "app/c.py"),
        _comp("test:tests.test_b", "test", "test_b", "tests/test_b.py",
              {"nodeid": "tests/test_b.py::test_b"}),
    ]
    edges = [
        _edge("e1", "module:app.a", "module:app.b", "calls"),
        _edge("e2", "test:tests.test_b", "module:app.b", "exercises"),
    ]
    return build_graph(comps, edges)


def test_dependents_and_covering_tests_predicted_honestly():
    res = changed_files_impact(_graph(), ["app/b.py"])
    assert res["summary"]["files_mapped"] == 1
    aff = {a["id"]: a for a in res["affected"]}
    # a depends on b: possible, never definite — static reachability is not proof
    assert aff["module:app.a"]["certainty"] == "possible"
    assert aff["module:app.a"]["distance"] == 1
    # the test exercises b: may_break, never fail
    t = {x["id"]: x for x in res["tests_may_break"]}["test:tests.test_b"]
    assert t["prediction"] == "may_break"
    assert res["uncovered_files"] == []
    assert "definite" not in str(res["summary"])


def test_changed_file_with_no_covering_test_is_reported():
    res = changed_files_impact(_graph(), ["app/a.py"])
    assert res["uncovered_files"] == ["app/a.py"]
    assert res["summary"]["files_without_test_coverage"] == 1
    assert res["tests_may_break"] == []


def test_unmapped_files_reported_not_ignored():
    res = changed_files_impact(_graph(), ["app/b.py", "does/not/exist.py"])
    assert res["summary"]["files_mapped"] == 1
    assert res["summary"]["files_unmapped"] == 1
    assert res["unmapped_files"][0]["file"] == "does/not/exist.py"
    assert "reason" in res["unmapped_files"][0]


def test_cycles_terminate_and_both_sides_reported():
    comps = [
        _comp("module:x", "module", "x", "x.py"),
        _comp("module:y", "module", "y", "y.py"),
    ]
    edges = [
        _edge("e1", "module:x", "module:y", "calls"),
        _edge("e2", "module:y", "module:x", "calls"),
    ]
    res = changed_files_impact(build_graph(comps, edges), ["x.py"])
    assert {a["id"] for a in res["affected"]} == {"module:y"}


def test_diff_prefixes_and_separators_normalised():
    assert normalise_path("a/app/b.py") == "app/b.py"
    assert normalise_path("b/app/b.py") == "app/b.py"
    assert normalise_path("./app/b.py") == "app/b.py"
    assert normalise_path("app\\b.py") == "app/b.py"
    res = changed_files_impact(_graph(), ["a/app/b.py"])
    assert res["summary"]["files_mapped"] == 1


def test_output_is_deterministic():
    g = _graph()
    files = ["app/b.py", "app/a.py"]
    one = changed_files_impact(g, files)
    two = changed_files_impact(g, list(reversed(files)))
    assert one == two


def test_empty_change_set_changes_nothing():
    res = changed_files_impact(_graph(), ["app/c.py"])
    assert res["affected"] == [] and res["tests_may_break"] == []
    assert res["summary"]["files_unmapped"] == 0  # c.py exists in the graph


# --- API -------------------------------------------------------------------


def test_api_changed_files_on_sample(client, sample_project):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Component

    db = SessionLocal()
    try:
        target_file = db.scalars(
            select(Component.file).where(Component.type == "module").limit(1)).first()
    finally:
        db.close()
    assert target_file
    r = client.post(f"/api/projects/{sample_project}/impact/changed-files", json={"files": [target_file]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["files_mapped"] == 1
    assert body["run_id"]
    assert all(a["certainty"] == "possible" for a in body["affected"])
    assert body["limitations"]


def test_api_changed_files_validates_input(client, sample_project):
    assert client.post(f"/api/projects/{sample_project}/impact/changed-files",
                       json={"files": []}).status_code == 422
    assert client.post(f"/api/projects/{sample_project}/impact/changed-files",
                       json={"files": ["x.py"] * 201}).status_code == 422


def test_api_changed_files_unknown_project(client):
    r = client.post("/api/projects/nope/impact/changed-files", json={"files": ["x.py"]})
    assert r.status_code == 404


def test_api_changed_files_requires_analysis(client):
    import io
    import zipfile

    def _zip(files: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, text in files.items():
                z.writestr(name, text)
        return buf.getvalue()

    r = client.post("/api/projects/upload",
                    files={"file": ("plain.zip", _zip({"app.py": "x = 1\n"}), "application/zip")})
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    try:
        # upload auto-analyses; clear the run pointer to exercise the guard
        from app import db as dbmod
        from app.models import Project

        db = dbmod.SessionLocal()
        try:
            db.get(Project, pid).latest_run_id = None
            db.commit()
        finally:
            db.close()
        r = client.post(f"/api/projects/{pid}/impact/changed-files", json={"files": ["x.py"]})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "not_analysed"
    finally:
        client.delete(f"/api/projects/{pid}")
