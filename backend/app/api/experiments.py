"""Predictions (deterministic impact) and controlled experiments."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.api.deps import get_project, pagination_params, require_run
from app.config import settings
from app.db import get_db
from app.experiments.runner import choose_mode, compare, execute, fault_spec
from app.graph.engine import compute_impact
from app.models import Experiment, Prediction, Project
from app.schemas import ChangedFilesIn, ExperimentIn, PredictionIn
from app.services.analysis import load_graph
from app.services.change_impact import changed_files_impact
from app.services.calibration import calibrate_impact, record_experiment_outcomes
from app.services.serialize import experiment_out, prediction_out, python_present

router = APIRouter(prefix="/api", tags=["predictions & experiments"])
log = logging.getLogger("devdna.experiments")
_exec_lock = threading.Lock()  # one experiment at a time per server process

STALE_STATUSES = ("queued", "running")
RESTART_ERROR = "Server restarted before this experiment finished — please re-run it."


def recover_stale_experiments() -> int:
    """Mark experiments left queued/running by a previous process as failed.

    A restart kills the background task that would have finished them, so they
    could never leave those states on their own. They are kept and marked
    failed with a re-run hint — never silently re-run, never deleted. Safe to
    call on every startup; a no-op when nothing is stale.
    """
    db = dbmod.SessionLocal()
    try:
        rows = db.scalars(select(Experiment).where(Experiment.status.in_(STALE_STATUSES))).all()
        for exp in rows:
            exp.status = "failed"
            exp.error = RESTART_ERROR
            exp.finished_at = datetime.now(timezone.utc)
        if rows:
            db.commit()
            log.warning("Marked %d stale queued/running experiment(s) as failed after restart.", len(rows))
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    cal_res = calibrate_impact(db, impact, body.scenario, project_id=p.id)
    active_risk = cal_res["score"]
    calibrated_risk = cal_res["calibrated_score"]
    heuristic_risk = cal_res["heuristic_score"]
    is_calibrated = cal_res["source"] == "calibrated"

    deterministic_assessment = {
        "engine": "DevDNA deterministic impact engine",
        "model": None,
        "ai_model_used": False,
        "method": (
            f"Calibrated empirical score ({calibrated_risk}) learned from {cal_res['sample_count']} experiment outcomes "
            f"of this project (calibration level: {cal_res['level']}); "
            f"heuristic score ({heuristic_risk}) retained during transition period for auditability; "
            f"not an AI prediction and not a failure probability."
            if is_calibrated else
            f"Heuristic score from definite, possible, and affected-route counts; "
            f"no calibrated outcome data for pattern yet ({cal_res['level']}: {cal_res['sample_count']} outcome(s) recorded, "
            f"20 needed; fallback to heuristic: {heuristic_risk}); "
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


@router.post("/projects/{project_id}/impact/changed-files")
def changed_files_impact_ep(project_id: str, body: ChangedFilesIn, db: Session = Depends(get_db)):
    """Predict the impact of a set of changed files (e.g. an open PR's diff)
    against the latest completed analysis. Deterministic and read-only: no
    prediction row is stored."""
    p = get_project(db, project_id)
    run = require_run(db, p)
    _, _, g = load_graph(db, run.id)
    result = changed_files_impact(g, body.files)
    result["project"] = {"id": p.id, "name": p.name}
    result["run_id"] = run.id
    return result


@router.get("/projects/{project_id}/predictions")
def list_predictions(project_id: str, db: Session = Depends(get_db), params: tuple[int, int] = Depends(pagination_params)):
    limit, offset = params
    get_project(db, project_id)
    total = db.scalar(select(func.count()).select_from(Prediction).where(Prediction.project_id == project_id))
    rows = db.scalars(select(Prediction).where(Prediction.project_id == project_id)
                      .order_by(Prediction.created_at.desc(), Prediction.id.desc()).offset(offset).limit(limit)).all()
    items = [prediction_out(x) for x in rows]
    return JSONResponse(content=items, headers={"X-Total-Count": str(total)})


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
    project = db.get(Project, pred.project_id)
    if not python_present(project):
        raise HTTPException(422, detail={
            "code": "analysis_only",
            "message": "Experiments require a Python pytest suite. This project is JavaScript/TypeScript-only: "
                       "analysis and predictions are available, but they cannot be verified by a test run.",
        })
    queued = db.scalar(select(func.count()).select_from(Experiment)
                       .where(Experiment.status.in_(["queued", "running"])))
    if queued >= settings.max_queued_experiments:
        raise HTTPException(429, detail={
            "code": "experiment_queue_full",
            "message": f"Experiment queue is full ({queued} queued/running, limit {settings.max_queued_experiments}). "
                       "Wait for running experiments to finish.",
        })
    exp = Experiment(project_id=pred.project_id, prediction_id=pred.id, status="queued",
                     approved_at=datetime.now(timezone.utc))
    db.add(exp)
    db.commit()
    background.add_task(run_experiment, exp.id)
    return experiment_out(exp)


@router.get("/projects/{project_id}/experiments")
def list_experiments(project_id: str, db: Session = Depends(get_db), params: tuple[int, int] = Depends(pagination_params)):
    limit, offset = params
    get_project(db, project_id)
    total = db.scalar(select(func.count()).select_from(Experiment).where(Experiment.project_id == project_id))
    rows = db.scalars(select(Experiment).where(Experiment.project_id == project_id)
                      .order_by(Experiment.created_at.desc(), Experiment.id.desc()).offset(offset).limit(limit)).all()
    out = []
    for e in rows:
        item = experiment_out(e)
        # keep list payloads small: drop raw logs
        obs = item["observation"]
        item["observation"] = {k: {kk: v.get(kk) for kk in ("counts", "duration_s", "timed_out")} for k, v in obs.items()
                               if k in ("baseline", "faulted")}
        out.append(item)
    return JSONResponse(content=out, headers={"X-Total-Count": str(total)})


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    exp = db.get(Experiment, experiment_id)
    if not exp:
        raise HTTPException(404, detail={"code": "experiment_not_found", "message": "Experiment not found"})
    return experiment_out(exp)


@router.get("/calibration/stats")
def get_calibration_stats(project_id: str | None = Query(None), db: Session = Depends(get_db)):
    """Recorded-outcome statistics.

    Scoped to one project when `project_id` is given; WITHOUT it the statistics
    pool every project's outcomes globally (deliberate and explicit: the
    calibration pool is global by default so cold-start projects benefit from
    earlier experiments, and this endpoint exposes that global pool).
    """
    from app.models import PredictionOutcome

    conditions = []
    if project_id:
        conditions.append(PredictionOutcome.project_id == project_id)
    rows = db.scalars(select(PredictionOutcome).where(*conditions)).all()
    guarded = [r for r in rows if r.guard_present]
    unguarded = [r for r in rows if not r.guard_present]
    return {
        "project_id": project_id,
        "total_recorded": len(rows),
        "guarded_count": len(guarded),
        "guarded_break_rate": round(sum(1 for r in guarded if r.broke) / len(guarded), 3) if guarded else None,
        "unguarded_count": len(unguarded),
        "unguarded_break_rate": round(sum(1 for r in unguarded if r.broke) / len(unguarded), 3) if unguarded else None,
    }
