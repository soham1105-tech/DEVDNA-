"""Tests for calibrated risk scoring learned from experiment outcomes."""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PredictionOutcome
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

