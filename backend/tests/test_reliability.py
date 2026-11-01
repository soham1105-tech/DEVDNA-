"""Module 5 — reliability memory: aggregation of recorded experiment outcomes."""
from datetime import datetime, timezone

import pytest

from app import db as dbmod
from app.models import PredictionOutcome
from app.services.calibration import build_pattern_key, MIN_SAMPLE_FOR_CALIBRATION
from app.services.reliability import reliability_memory


@pytest.fixture()
def db_session():
    db = dbmod.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _outcome(pid, scenario="db_unavailable", pred="fail", obs="failed", verdict="match",
             certainty="definite", handled=False, mocked=False, routes=0, exp="exp1",
             nodeid="tests/t.py::test_a", broke=None):
    if broke is None:
        broke = obs in ("failed", "error")
    return PredictionOutcome(
        project_id=pid, scenario=scenario, target_type="database", target_id="database:sqlite",
        test_nodeid=nodeid, guard_present=handled, test_mocked=mocked, certainty=certainty,
        routes_affected=routes, predicted_outcome=pred, observed_outcome=obs, verdict=verdict,
        broke=broke, pattern_key=build_pattern_key(handled, mocked, certainty, routes > 0),
        experiment_id=exp, created_at=datetime.now(timezone.utc),
    )


def test_empty_memory_is_honest(db_session):
    res = reliability_memory(db_session, project_id="no-such-project")
    assert res["status"] == "empty"
    assert res["summary"]["observations"] == 0
    assert "Run controlled experiments" in res["note"]
    assert res["limitations"]


def test_fail_accuracy_and_insufficient_data(db_session):
    db_session.add_all([_outcome("p-rel", exp=f"rel-e{i}", nodeid=f"tests/rel.py::t{i}") for i in range(5)])
    db_session.commit()
    res = reliability_memory(db_session, project_id="p-rel")
    s = res["by_scenario"][0]
    assert s["observations"] == 5 and s["experiments"] == 5
    # 5 samples < MIN_SAMPLE: reported as insufficient, not 100%
    assert s["fail_prediction_accuracy"]["status"] == "insufficient_data"
    assert s["fail_prediction_accuracy"]["sample"] == 5


def test_measured_rate_at_threshold(db_session):
    n = MIN_SAMPLE_FOR_CALIBRATION
    rows = [_outcome("p-rel2", exp=f"rel2-e{i}", nodeid=f"tests/rel2.py::t{i}") for i in range(n - 2)]
    rows += [_outcome("p-rel2", exp="rel2-x", nodeid="tests/rel2.py::xa", verdict="mismatch",
                      pred="fail", obs="passed", broke=False),
             _outcome("p-rel2", exp="rel2-x", nodeid="tests/rel2.py::xb", verdict="mismatch",
                      pred="fail", obs="passed", broke=False)]
    db_session.add_all(rows)
    db_session.commit()
    res = reliability_memory(db_session, project_id="p-rel2")
    s = res["by_scenario"][0]
    acc = s["fail_prediction_accuracy"]
    assert acc["status"] == "measured" and acc["sample"] == n
    assert acc["rate"] == round((n - 2) / n, 3)


def test_excluded_and_uncertain_counted_but_not_accuracy(db_session):
    rows = [_outcome("p-rel3", exp=f"rel3-e{i}", nodeid=f"tests/rel3.py::t{i}", verdict="excluded", obs="not_run", broke=False) for i in range(3)]
    rows += [_outcome("p-rel3", pred="may_fail", obs="passed", verdict="uncertain_passed",
                      broke=False, nodeid="tests/rel3.py::u1")]
    db_session.add_all(rows)
    db_session.commit()
    res = reliability_memory(db_session, project_id="p-rel3")
    s = res["by_scenario"][0]
    assert s["excluded_observations"] == 3
    assert s["uncertain_observations"] == 1
    assert s["fail_prediction_accuracy"]["status"] == "insufficient_data"  # no fail rows


def test_pattern_memory_splits_by_claim_shape(db_session):
    n = MIN_SAMPLE_FOR_CALIBRATION
    rows = [_outcome("p-rel4", exp=f"rel4-e{i}", nodeid=f"tests/rel4.py::a{i}", handled=True) for i in range(n)]
    rows += [_outcome("p-rel4", exp=f"e{i}", nodeid=f"tests/t.py::b{i}", handled=False) for i in range(2)]
    db_session.add_all(rows)
    db_session.commit()
    res = reliability_memory(db_session, project_id="p-rel4")
    patterns = {p["pattern_key"]: p for p in res["by_pattern"]}
    assert len(patterns) == 2
    guarded = [p for p in patterns.values() if p["guard_present"]][0]
    assert guarded["observations"] == n
    assert guarded["contradiction_rate"]["status"] == "measured"
    unguarded = [p for p in patterns.values() if not p["guard_present"]][0]
    assert unguarded["contradiction_rate"]["status"] == "insufficient_data"


def test_project_scoping_isolates_memory(db_session):
    db_session.add(_outcome("p-isoA", exp="reliso-eA", nodeid="tests/reliso.py::a"))
    db_session.commit()
    a = reliability_memory(db_session, project_id="p-isoA")
    assert a["summary"]["observations"] >= 1
    glob = reliability_memory(db_session, project_id=None)
    assert glob["summary"]["observations"] >= a["summary"]["observations"]
    assert glob["scope"] == "global"


# --- API --------------------------------------------------------------------


def test_api_project_reliability(client, sample_project, db_session):
    r = client.get(f"/api/projects/{sample_project}/reliability")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == sample_project
    assert "method" in body and body["limitations"]


def test_api_global_reliability_and_unknown_project(client):
    assert client.get("/api/reliability").status_code == 200
    assert client.get("/api/reliability", params={"project_id": "whatever"}).status_code == 200
    assert client.get("/api/projects/nope/reliability").status_code == 404
