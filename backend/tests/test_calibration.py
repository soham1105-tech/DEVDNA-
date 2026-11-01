"""Tests for calibrated risk scoring learned from experiment outcomes."""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PredictionOutcome
from app.services.calibration import build_pattern_key
from app.services.calibration import (
    seed_calibration_outcomes,
)


def test_outcome_persistence_from_experiment(client, sample_project):
    """Confirm every experiment execution persists labelled outcomes in prediction_outcomes table."""
    pred_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "external_api_unavailable", "target_id": "external:SHIPPING_API_URL"},
    )
    assert pred_res.status_code == 201
    pred_id = pred_res.json()["id"]

    exp_res = client.post(f"/api/predictions/{pred_id}/experiments", json={"approved": True})
    assert exp_res.status_code == 202
    exp_id = exp_res.json()["id"]

    from app.api.experiments import run_experiment
    run_experiment(exp_id)

    db = SessionLocal()
    try:
        outcomes = db.scalars(
            select(PredictionOutcome).where(PredictionOutcome.experiment_id == exp_id)
        ).all()
        assert len(outcomes) > 0, "Experiment must record prediction outcomes"
        for o in outcomes:
            assert o.scenario == "external_api_unavailable"
            assert o.target_type == "external_api"
            assert o.predicted_outcome in ("fail", "pass", "may_fail")
            assert o.observed_outcome in ("passed", "failed", "error")
            assert isinstance(o.guard_present, bool)
            assert isinstance(o.test_mocked, bool)
            assert isinstance(o.broke, bool)
            assert o.pattern_key.startswith("guard=")
    finally:
        db.close()


def test_transition_period_displays_both_scores_and_fallback(client, sample_project):
    """When no calibration data exists for a pattern, falls back to heuristic and labels it auditable."""
    db = SessionLocal()
    try:
        # Clear outcomes to test cold-start fallback
        db.query(PredictionOutcome).delete()
        db.commit()
    finally:
        db.close()

    pred_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "db_unavailable", "target_id": "database:sqlite"},
    )
    assert pred_res.status_code == 201
    pred = pred_res.json()
    sim = pred["impact"]["neuro_symbolic_simulation"]

    # Both scores must be present and clearly labelled during transition period
    assert "cascading_risk_score" in sim
    assert "calibrated_risk_score" in sim
    assert "heuristic_risk_score" in sim
    assert "score_source" in sim
    assert sim["score_source"] == "heuristic_fallback"
    assert sim["calibrated_risk_score"] is None
    assert sim["heuristic_risk_score"] > 0
    assert sim["cascading_risk_score"] == sim["heuristic_risk_score"]
    assert "fallback to heuristic" in sim["method"].lower() or "no outcome data" in sim["method"].lower()


def test_seeded_break_rates_differentiate_guarded_and_unguarded(client, sample_project):
    """Verify: seed outcomes where guarded routes break 10% of the time and unguarded
    routes break 85% of the time, and confirm the calibrated score reflects that difference
    while the old formula would not.
    """
    db = SessionLocal()
    try:
        # Seed 10% break rate for guarded and 85% for unguarded
        seed_calibration_outcomes(
            db,
            guarded_break_rate=0.10,
            unguarded_break_rate=0.85,
            sample_size=100,
            scenario="external_api_unavailable",
            target_type="external_api",
            project_id=sample_project,
        )
    finally:
        db.close()

    # 1. Prediction for unguarded route (shipping API on orders/shipping has no defensive try/catch guard)
    unguarded_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "external_api_unavailable", "target_id": "external:SHIPPING_API_URL"},
    )
    assert unguarded_res.status_code == 201
    unguarded_sim = unguarded_res.json()["impact"]["neuro_symbolic_simulation"]

    # 2. Prediction for guarded route (worker notifications on orders are wrapped in try/except WorkerUnavailable)
    guarded_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "worker_unavailable", "target_id": "worker:shoplite.worker.NotificationWorker"},
    )
    assert guarded_res.status_code == 201
    guarded_sim = guarded_res.json()["impact"]["neuro_symbolic_simulation"]

    # Check that calibrated risk reflects the empirical break rates (approx 85% vs 10%)
    unguarded_cal = unguarded_sim["calibrated_risk_score"]
    guarded_cal = guarded_sim["calibrated_risk_score"]
    assert unguarded_cal is not None, f"Expected calibrated score for unguarded, got {unguarded_sim}"
    assert guarded_cal is not None, f"Expected calibrated score for guarded, got {guarded_sim}"

    # Unguarded should be around 85%, guarded should be around 10%
    assert 80.0 <= unguarded_cal <= 90.0, f"Unguarded calibrated score should reflect ~85%, got {unguarded_cal}"
    assert 5.0 <= guarded_cal <= 15.0, f"Guarded calibrated score should reflect ~10%, got {guarded_cal}"

    # Active cascading_risk_score uses the calibrated score
    assert unguarded_sim["cascading_risk_score"] == unguarded_cal
    assert guarded_sim["cascading_risk_score"] == guarded_cal

    # Contrast with old heuristic formula:
    # Notice that both have both scores preserved for auditing
    assert unguarded_sim["heuristic_risk_score"] > 0
    assert guarded_sim["heuristic_risk_score"] > 0
    # In the old heuristic, worker scenario would still score high because routes_affected=1, definite=1 etc.
    # But calibrated score drastically separates them based on the 85% vs 10% reality.
    assert unguarded_cal - guarded_cal >= 70.0, (
        f"Calibrated scores must drastically differentiate (got unguarded={unguarded_cal}, guarded={guarded_cal})"
    )

    # Auditable labelling
    assert unguarded_sim["score_source"] == "calibrated"
    assert guarded_sim["score_source"] == "calibrated"
    assert "calibrated empirical score" in unguarded_sim["method"].lower()
    assert "heuristic score" in unguarded_sim["method"].lower()



# --- (a)-(e) regression tests ------------------------------------------------

def _fresh_project(client):
    """A brand-new demo project, so calibration pools are unpolluted."""
    return client.post("/api/projects/sample").json()["project"]["id"]


def _record_outcomes(db, experiment, prediction, rows):
    from app.services.calibration import record_experiment_outcomes

    return record_experiment_outcomes(db, experiment, prediction, {"rows": rows})


def test_calibration_at_n_one_is_insufficient_not_a_fluke(client):
    """One recorded outcome must not produce a 0% or 100% calibrated score."""
    from app.services.calibration import calibrate_impact

    project = _fresh_project(client)
    db = SessionLocal()
    try:
        for i in range(1):
            db.add(PredictionOutcome(
                project_id=project, scenario="external_api_unavailable",
                target_type="external_api", target_id="external:SHIPPING_API_URL",
                test_nodeid=f"lone_{i}", guard_present=False, test_mocked=False,
                certainty="definite", routes_affected=1, predicted_outcome="fail",
                observed_outcome="failed", verdict="match", broke=True,
                pattern_key=build_pattern_key(False, False, "definite", True),
            ))
        db.commit()
        # build a real impact for the same pattern
        pred_res = client.post(f"/api/projects/{project}/predictions",
                               json={"scenario": "external_api_unavailable",
                                     "target_id": "external:SHIPPING_API_URL"})
        impact = pred_res.json()["impact"]
        cal = calibrate_impact(db, impact, "external_api_unavailable", project_id=project)
    finally:
        db.close()

    assert cal["source"] == "insufficient_data"
    assert cal["calibrated_score"] is None
    assert cal["level"] == "insufficient_data"
    assert 1 <= cal["sample_count"] < 20
    assert cal["score"] == cal["heuristic_score"]  # heuristic fallback active
    assert "Insufficient data" in cal["note"]


def test_mixed_certainty_prediction_matches_both_outcome_kinds(client):
    """A 'mixed' impact prediction (definite and possible nodes) draws evidence
    from recorded outcomes of either certainty."""
    from app.services.calibration import get_pattern_break_rate

    project = _fresh_project(client)
    db = SessionLocal()
    try:
        for i in range(25):
            db.add(PredictionOutcome(
                project_id=project, scenario="db_unavailable",
                target_type="database", target_id="database:sqlite",
                test_nodeid=f"mixed_definite_{i}", guard_present=False,
                test_mocked=False, certainty="definite", routes_affected=1,
                predicted_outcome="fail", observed_outcome="failed",
                verdict="match", broke=True,
                pattern_key=build_pattern_key(False, False, "definite", True),
            ))
            db.add(PredictionOutcome(
                project_id=project, scenario="db_unavailable",
                target_type="database", target_id="database:sqlite",
                test_nodeid=f"mixed_possible_{i}", guard_present=False,
                test_mocked=False, certainty="possible", routes_affected=1,
                predicted_outcome="may_fail", observed_outcome="failed",
                verdict="match", broke=False,
                pattern_key=build_pattern_key(False, False, "possible", True),
            ))
        db.commit()

        rate, total, level = get_pattern_break_rate(
            db, guard_present=False, certainty="mixed", has_routes=True,
            project_id=project,
        )
    finally:
        db.close()

    assert total == 50  # both certainty kinds count toward a mixed lookup
    assert rate == 0.5  # 25 broke of 50
    assert level == "general_pattern_features"


def test_delete_project_removes_its_outcomes(client):
    from app.models import PredictionOutcome

    project = _fresh_project(client)
    pred_res = client.post(f"/api/projects/{project}/predictions",
                           json={"scenario": "external_api_unavailable",
                                 "target_id": "external:SHIPPING_API_URL"})
    pred_id = pred_res.json()["id"]
    exp_res = client.post(f"/api/predictions/{pred_id}/experiments", json={"approved": True})
    exp_id = exp_res.json()["id"]
    from app.api.experiments import run_experiment
    run_experiment(exp_id)

    db = SessionLocal()
    try:
        before = db.query(PredictionOutcome).filter(PredictionOutcome.project_id == project).count()
        assert before > 0
    finally:
        db.close()

    assert client.delete(f"/api/projects/{project}").status_code == 204

    db = SessionLocal()
    try:
        assert db.query(PredictionOutcome).filter(
            PredictionOutcome.project_id == project).count() == 0
    finally:
        db.close()


def test_double_recorded_experiment_is_counted_once(client, sample_project):
    """Re-recording an experiment's outcomes replaces the earlier rows, and the
    schema enforces uniqueness on (experiment_id, test_nodeid)."""
    from sqlalchemy import text as sql_text

    from app.models import Experiment, Prediction, PredictionOutcome

    pred_res = client.post(f"/api/projects/{sample_project}/predictions",
                           json={"scenario": "external_api_unavailable",
                                 "target_id": "external:SHIPPING_API_URL"})
    pred_id = pred_res.json()["id"]

    db = SessionLocal()
    try:
        pred = db.get(Prediction, pred_id)
        exp = Experiment(project_id=sample_project, prediction_id=pred_id,
                         status="experiment_executed", mode="local_harness")
        db.add(exp)
        db.commit()

        rows = [{"nodeid": "tests/t.py::test_a", "verdict": "match",
                 "prediction": "fail", "observed": "failed", "handled_at": None}]
        first = _record_outcomes(db, exp, pred, rows)
        db.commit()
        second = _record_outcomes(db, exp, pred, rows)
        db.commit()

        count = db.query(PredictionOutcome).filter(
            PredictionOutcome.experiment_id == exp.id).count()
        assert count == len(first) == len(second) == 1  # not doubled

        index = db.execute(sql_text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_prediction_outcomes_experiment_nodeid'")).scalar()
        assert index == "uq_prediction_outcomes_experiment_nodeid"
    finally:
        db.close()
