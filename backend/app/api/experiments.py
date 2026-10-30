"""Predictions (deterministic impact) and controlled experiments."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.api.deps import get_project, require_run
from app.db import get_db
from app.experiments.runner import choose_mode, compare, execute, fault_spec
from app.graph.engine import compute_impact
from app.models import Experiment, Prediction, Project
from app.schemas import ExperimentIn, PredictionIn
from app.services.analysis import load_graph
from app.services.calibration import calibrate_impact, record_experiment_outcomes
from app.services.serialize import experiment_out, prediction_out

router = APIRouter(prefix="/api", tags=["predictions & experiments"])
log = logging.getLogger("devdna.experiments")
_exec_lock = threading.Lock()  # one experiment at a time per server process


@router.post("/projects/{project_id}/predictions", status_code=201)
def create_prediction(project_id: str, body: PredictionIn, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    run = require_run(db, p)
    comps, edges, g = load_graph(db, run.id)
    try:
        impact = compute_impact(g, body.scenario, body.target_id, custom_spec=body.custom_spec)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "invalid_scenario", "message": str(exc)})

    definite_count = impact["summary"]["definite"]
    possible_count = impact["summary"]["possible"]
    routes_count = impact["summary"]["routes_affected"]
    tests_fail = impact["summary"]["tests_predicted_fail"]

    # Calibrate risk score using empirically observed break rates from real experiment outcomes
    cal_res = calibrate_impact(db, impact, body.scenario)
    active_risk = cal_res["score"]
    calibrated_risk = cal_res["calibrated_score"]
    heuristic_risk = cal_res["heuristic_score"]
    is_calibrated = cal_res["source"] == "calibrated"

    deterministic_assessment = {
        "engine": "DevDNA deterministic impact engine",
        "model": None,
        "ai_model_used": False,
        "method": (
            f"Calibrated empirical score ({calibrated_risk}) learned from {cal_res['sample_count']} experiment outcomes; "
            f"heuristic score ({heuristic_risk}) retained during transition period for auditability; "
            f"not an AI prediction and not a failure probability."
            if is_calibrated else
            f"Heuristic score from definite, possible, and affected-route counts; "
            f"no outcome data for pattern yet (fallback to heuristic: {heuristic_risk}); "
            f"not an AI prediction and not a failure probability."
        ),
        "cascading_risk_score": active_risk,
        "calibrated_risk_score": calibrated_risk,
        "heuristic_risk_score": heuristic_risk,
        "score_source": cal_res["source"],
        "calibration": cal_res,
        "semantic_summary": (
            f"Failure of '{impact['target']['name']}' under scenario '{impact['scenario_label']}' "
            f"directly endangers {routes_count} HTTP route(s) and cascades across {definite_count} definite "
            f"and {possible_count} conditionally-handled path(s)."
        ),
        "runtime_vulnerabilities": [
            f"Target component '{impact['target']['name']}' exposes {len(impact.get('relevant_exceptions', []))} failure modes.",
            "Conditional try/except guards on 'possible' paths may suppress exceptions without recovering consistent application state.",
        ] if possible_count > 0 else [
            f"Unmitigated propagation: no defensive exception guards detected along execution paths from '{impact['target']['name']}'."
        ],
        "test_coverage_gap": (
            [f"{tests_fail} test(s) predicted to fail, but {routes_count} user-facing route(s) have unverified error handling."]
            if tests_fail < routes_count else ["Adequate test suite mapping detected for reachable fault paths."]
        ),
        "blast_radius_assessment": "CRITICAL" if active_risk > 70 else ("HIGH" if active_risk > 40 else "MODERATE"),
        "neuro_symbolic": False,
    }
    impact["neuro_symbolic_simulation"] = deterministic_assessment

    pred = Prediction(project_id=p.id, run_id=run.id, scenario=body.scenario, target_id=body.target_id, impact=impact)
    db.add(pred)
    db.commit()
    return prediction_out(pred)


@router.get("/projects/{project_id}/predictions")
def list_predictions(project_id: str, db: Session = Depends(get_db)):
    get_project(db, project_id)
    rows = db.scalars(select(Prediction).where(Prediction.project_id == project_id).order_by(Prediction.created_at.desc())).all()
    return [prediction_out(x) for x in rows]


@router.get("/predictions/{prediction_id}")
def get_prediction(prediction_id: str, db: Session = Depends(get_db)):
    pred = db.get(Prediction, prediction_id)
    if not pred:
        raise HTTPException(404, detail={"code": "prediction_not_found", "message": "Prediction not found"})
    return prediction_out(pred)


def run_experiment(experiment_id: str) -> None:
    db = dbmod.SessionLocal()
    try:
        exp = db.get(Experiment, experiment_id)
        pred = exp.prediction
        project = db.get(Project, exp.project_id)
        mode, reason = choose_mode(project.trusted)
        exp.mode = mode
        if mode == "blocked":
            exp.status, exp.error = "blocked", reason
            exp.finished_at = datetime.now(timezone.utc)
            db.commit()
            return
        with _exec_lock:
            exp.status, exp.started_at = "running", datetime.now(timezone.utc)
            db.commit()
            observation = execute(Path(project.source_path), fault_spec(pred.impact), mode)
        comparison = compare(pred.impact, observation)
        exp.observation = observation
        exp.comparison = comparison
        exp.status = comparison["status"]
        exp.finished_at = datetime.now(timezone.utc)
        record_experiment_outcomes(db, exp, pred, comparison)
        db.commit()
    except Exception as exc:
        log.exception("experiment failed")
        db.rollback()
        exp = db.get(Experiment, experiment_id)
        if exp:
            exp.status, exp.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
            exp.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/predictions/{prediction_id}/experiments", status_code=202)
def start_experiment(prediction_id: str, body: ExperimentIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    pred = db.get(Prediction, prediction_id)
    if not pred:
        raise HTTPException(404, detail={"code": "prediction_not_found", "message": "Prediction not found"})
    if not body.approved:
        raise HTTPException(400, detail={"code": "approval_required",
                                         "message": "Experiments only run after explicit approval (approved: true)."})
    exp = Experiment(project_id=pred.project_id, prediction_id=pred.id, status="queued",
                     approved_at=datetime.now(timezone.utc))
    db.add(exp)
    db.commit()
    background.add_task(run_experiment, exp.id)
    return experiment_out(exp)


@router.get("/projects/{project_id}/experiments")
def list_experiments(project_id: str, db: Session = Depends(get_db)):
    get_project(db, project_id)
    rows = db.scalars(select(Experiment).where(Experiment.project_id == project_id).order_by(Experiment.created_at.desc())).all()
    out = []
    for e in rows:
        item = experiment_out(e)
        # keep list payloads small: drop raw logs
        obs = item["observation"]
        item["observation"] = {k: {kk: v.get(kk) for kk in ("counts", "duration_s", "timed_out")} for k, v in obs.items()
                               if k in ("baseline", "faulted")}
        out.append(item)
    return out


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    exp = db.get(Experiment, experiment_id)
    if not exp:
        raise HTTPException(404, detail={"code": "experiment_not_found", "message": "Experiment not found"})
    return experiment_out(exp)


@router.get("/calibration/stats")
def get_calibration_stats(db: Session = Depends(get_db)):
    from app.models import PredictionOutcome
    rows = db.scalars(select(PredictionOutcome)).all()
    guarded = [r for r in rows if r.guard_present]
    unguarded = [r for r in rows if not r.guard_present]
    return {
        "total_recorded": len(rows),
        "guarded_count": len(guarded),
        "guarded_break_rate": round(sum(1 for r in guarded if r.broke) / len(guarded), 3) if guarded else None,
        "unguarded_count": len(unguarded),
        "unguarded_break_rate": round(sum(1 for r in unguarded if r.broke) / len(unguarded), 3) if unguarded else None,
    }
