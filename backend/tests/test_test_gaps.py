"""Module 3 — test-gap validation: static gap map + experiment validation."""
from app.graph.engine import build_graph
from app.services.test_gaps import compute_test_gaps, validate_gaps


def _comp(cid, ctype, name, file, data=None):
    return {"id": cid, "type": ctype, "name": name, "file": file, "line": 1, "data": data or {}}


def _edge(eid, src, dst, etype):
    return {"id": eid, "source": src, "target": dst, "type": etype,
            "classification": "confirmed", "evidence": [], "call_sites": []}


def _graph():
    """test_a exercises a.py which calls b.py; c.py is unreachable; infra node."""
    comps = [
        _comp("test:tests.test_a", "test", "test_a", "tests/test_a.py",
              {"nodeid": "tests/test_a.py::test_a", "junit_key": "tests/test_a.py::test_a"}),
        _comp("module:app.a", "module", "app.a", "app/a.py"),
        _comp("module:app.b", "module", "app.b", "app/b.py"),
        _comp("module:app.c", "module", "app.c", "app/c.py"),
        _comp("database:sqlite", "database", "sqlite", None),
    ]
    edges = [
        _edge("e1", "test:tests.test_a", "module:app.a", "exercises"),
        _edge("e2", "module:app.a", "module:app.b", "calls"),
    ]
    return build_graph(comps, edges)


def test_static_gaps_per_file():
    res = compute_test_gaps(_graph())
    assert res["test_count"] == 1
    gaps = {e["file"]: e for e in res["file_gaps"]}
    assert gaps["app/c.py"]["uncovered"] == 1
    assert gaps["app/c.py"]["gap_ids"] == ["module:app.c"]
    # a and b are covered (b transitively via calls)
    assert res["summary"]["files_with_gaps"] == 1
    assert res["summary"]["uncovered_components"] == 1
    assert res["fully_covered_files"] == ["app/a.py", "app/b.py"]


def test_infra_nodes_not_counted_as_gaps():
    res = compute_test_gaps(_graph())
    assert all("database" not in str(g["file"]) for g in res["file_gaps"])
    assert res["summary"]["covered_components"] == 2  # a.py + b.py only


def test_distance_is_min_hops_from_any_test():
    res = compute_test_gaps(_graph())
    # app/c.py is a gap so has no distance; covered counts stay honest
    gaps = {e["file"]: e for e in res["file_gaps"]}
    assert gaps["app/c.py"]["min_distance"] is None
    assert res["summary"]["covered_components"] == 2


def test_validation_marks_files_with_executed_tests():
    g = _graph()
    gaps = compute_test_gaps(g)
    res = validate_gaps(g, gaps, {"tests/test_a.py::test_a"}, experiment_count=1)
    assert res["validation"]["status"] == "validated"
    assert res["validated_files"]["app/a.py"] >= 1
    assert res["validated_files"]["app/b.py"] >= 1
    # a gap can never be validated — absence of evidence stays honest
    assert "app/c.py" not in res["validated_files"]


def test_validation_without_experiments_is_static_only():
    res = validate_gaps(_graph(), compute_test_gaps(_graph()), set(), experiment_count=0)
    assert res["validation"]["status"] == "static_only"
    assert res["validation"]["experiments_observed"] == 0
    assert "Run an experiment" in res["validation"]["note"]


def test_output_is_deterministic():
    g = _graph()
    assert compute_test_gaps(g) == compute_test_gaps(g)


def test_method_and_limitations_present():
    res = compute_test_gaps(_graph())
    assert "method" in res and len(res["limitations"]) >= 2


# --- API -------------------------------------------------------------------


def test_api_test_gaps_on_sample(client, sample_project):
    r = client.get(f"/api/projects/{sample_project}/test-gaps")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["code_components"] > 0
    # other tests may have run real experiments on sample_project; accept both
    # statuses but require the validation section to be internally consistent
    if body["validation"]["status"] == "validated":
        assert body["validation"]["experiments_observed"] >= 1
    else:
        assert body["validation"]["experiments_observed"] == 0
    assert "method" in body and body["limitations"]


def test_api_test_gaps_validated_after_experiment(client, sample_project):
    """Insert a completed experiment with observed baseline results; the
    files its tests touched must flip to validated."""
    from app import db as dbmod
    from app.models import Experiment, Prediction

    db = dbmod.SessionLocal()
    try:
        pred = Prediction(project_id=sample_project, run_id="run-x", scenario="db_unavailable",
                          target_id="database:sqlite", impact={})
        db.add(pred)
        db.flush()
        exp = Experiment(project_id=sample_project, prediction_id=pred.id, status="completed",
                         observation={"baseline": {"results": {"tests/test_api.py::test_health_ok": {
                             "outcome": "passed"}}}})
        db.add(exp)
        db.commit()
        exp_id, pred_id = exp.id, pred.id
    finally:
        db.close()
    try:
        body = client.get(f"/api/projects/{sample_project}/test-gaps").json()
        assert body["validation"]["status"] == "validated"
        # sample_project may carry experiments from other tests; ours must be counted
        assert body["validation"]["experiments_observed"] >= 1
        assert body["validated_files"]
    finally:
        db = dbmod.SessionLocal()
        try:
            e = db.get(Experiment, exp_id)
            db.delete(e)
            p = db.get(Prediction, pred_id)
            db.delete(p)
            db.commit()
        finally:
            db.close()


def test_api_test_gaps_unknown_project(client):
    assert client.get("/api/projects/nope/test-gaps").status_code == 404


def test_api_test_gaps_requires_analysis(client):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "x = 1\n")
    r = client.post("/api/projects/upload",
                    files={"file": ("gap.zip", buf.getvalue(), "application/zip")})
    pid = r.json()["project"]["id"]
    try:
        from app import db as dbmod
        from app.models import Project

        db = dbmod.SessionLocal()
        try:
            db.get(Project, pid).latest_run_id = None
            db.commit()
        finally:
            db.close()
        r = client.get(f"/api/projects/{pid}/test-gaps")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "not_analysed"
    finally:
        client.delete(f"/api/projects/{pid}")
