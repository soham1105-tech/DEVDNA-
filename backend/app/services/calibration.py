"""Calibration service for learning failure break rates from real experiment outcomes.

Replaces fixed heuristic scoring weights (definite*15 + possible*8 + routes*20)
with empirically observed break rates per pattern (guard present, test mocked,
certainty, routes affected).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Experiment, Prediction, PredictionOutcome

log = logging.getLogger("devdna.calibration")


def build_pattern_key(
    guard_present: bool,
    test_mocked: bool = False,
    certainty: str = "definite",
    has_routes: bool = True,
) -> str:
    """Build a deterministic pattern key for indexing and grouping."""
    return f"guard={guard_present}|mocked={test_mocked}|certainty={certainty}|routes={has_routes}"


def record_experiment_outcomes(
    db: Session,
    experiment: Experiment,
    prediction: Prediction | None = None,
    comparison: dict | None = None,
) -> list[PredictionOutcome]:
    """Persist labelled test outcomes from an experiment run into the database."""
    pred = prediction or experiment.prediction
    comp = comparison if comparison is not None else (experiment.comparison or {})
    if not pred or not comp:
        return []

    impact = pred.impact or {}
    tests_by_nodeid = {t.get("nodeid"): t for t in impact.get("tests", []) if t.get("nodeid")}
    routes_count = impact.get("summary", {}).get("routes_affected", 0)
    target_type = impact.get("target", {}).get("type", "unknown")
    target_id = pred.target_id
    scenario = pred.scenario

    rows = comp.get("rows", [])
    records: list[PredictionOutcome] = []

    for r in rows:
        nodeid = r.get("nodeid") or r.get("test")
        verdict = r.get("verdict", "")
        # Tests that were not run or did not pass baseline cannot provide valid evidence
        if verdict == "excluded":
            continue

        pred_outcome = r.get("prediction", "pass")
        obs_outcome = r.get("observed", "not_run")
        handled = bool(r.get("handled_at"))
        test_info = tests_by_nodeid.get(nodeid, {})
        mocked = bool(test_info.get("mocked", False))
        certainty = test_info.get("certainty") or ("possible" if handled else "definite")
        has_routes = routes_count > 0
        broke = obs_outcome in ("failed", "error")

        pkey = build_pattern_key(handled, mocked, certainty, has_routes)

        outcome_rec = PredictionOutcome(
            experiment_id=experiment.id,
            prediction_id=pred.id,
            project_id=pred.project_id,
            scenario=scenario,
            target_type=target_type,
            target_id=target_id,
            test_nodeid=nodeid,
            guard_present=handled,
            test_mocked=mocked,
            certainty=certainty,
            routes_affected=routes_count,
            predicted_outcome=pred_outcome,
            observed_outcome=obs_outcome,
            verdict=verdict,
            broke=broke,
            pattern_key=pkey,
            created_at=datetime.now(timezone.utc),
        )
        db.add(outcome_rec)
        records.append(outcome_rec)

    try:
        db.flush()
    except Exception as exc:
        log.warning("Failed to flush experiment outcomes: %s", exc)

    return records


def _routes_filter(has_routes: bool):
    """Return a SQLAlchemy filter for routes_affected > 0 or == 0."""
    if has_routes:
        return PredictionOutcome.routes_affected > 0
    return PredictionOutcome.routes_affected == 0


def get_pattern_break_rate(
    db: Session,
    guard_present: bool,
    test_mocked: bool = False,
    certainty: str = "definite",
    has_routes: bool = True,
    scenario: str | None = None,
    target_type: str | None = None,
) -> tuple[float | None, int, str]:
    """Calculate the observed break rate for a given pattern.
    
    Uses hierarchical fallback if exact scenario/type match has insufficient data:
    Level 1: Exact scenario + target_type + pattern features
    Level 2: Pattern features (guard_present + test_mocked + certainty + has_routes)
    Level 3: Guard present + has_routes
    Level 4: Guard present only
    """
    base_pattern = [
        PredictionOutcome.guard_present == guard_present,
        PredictionOutcome.test_mocked == test_mocked,
        PredictionOutcome.certainty == certainty,
        _routes_filter(has_routes),
    ]

    levels: list[tuple[str, list]] = [
        # Level 1: Full context (only if scenario and target_type provided)
        (
            "exact_scenario_and_pattern",
            base_pattern + [
                PredictionOutcome.scenario == scenario,
                PredictionOutcome.target_type == target_type,
            ],
        ),
        # Level 2: General pattern features across scenarios
        ("general_pattern_features", list(base_pattern)),
        # Level 3: Guard + routes
        (
            "guard_and_routes",
            [
                PredictionOutcome.guard_present == guard_present,
                _routes_filter(has_routes),
            ],
        ),
        # Level 4: Guard only
        (
            "guard_only",
            [PredictionOutcome.guard_present == guard_present],
        ),
    ]

    # Skip level 1 when scenario/target_type are None (no point filtering on None)
    if scenario is None or target_type is None:
        levels = levels[1:]

    for level_name, conditions in levels:
        query = select(PredictionOutcome).where(*conditions)
        outcomes = db.scalars(query).all()
        if outcomes:
            broke_count = sum(1 for o in outcomes if o.broke)
            total = len(outcomes)
            rate = broke_count / total
            return rate, total, level_name

    return None, 0, "no_data"


def calibrate_impact(db: Session, impact: dict, scenario: str) -> dict[str, Any]:
    """Compute the calibrated score and retain the heuristic score for auditing."""
    summary = impact.get("summary", {})
    definite_count = summary.get("definite", 0)
    possible_count = summary.get("possible", 0)
    routes_count = summary.get("routes_affected", 0)

    # Old heuristic formula
    raw_heuristic = min(100.0, (definite_count * 15.0) + (possible_count * 8.0) + (routes_count * 20.0))
    heuristic_score = round(raw_heuristic, 1)

    # Determine pattern features from impact
    affected_routes = [a for a in impact.get("affected", []) if a.get("type") == "route"]
    if affected_routes:
        # If routes are affected, check if they have protective guards
        guard_present = any(bool(r.get("handled_at")) for r in affected_routes)
    else:
        guard_present = any(bool(a.get("handled_at")) for a in impact.get("affected", [])) or (possible_count > 0)

    test_mocked = any(bool(t.get("mocked")) for t in impact.get("tests", []) if t.get("path"))
    certainty = "possible" if (possible_count > 0 and definite_count == 0) else ("definite" if possible_count == 0 else "mixed")
    target_type = impact.get("target", {}).get("type", "unknown")
    has_routes = routes_count > 0

    break_rate, sample_count, level = get_pattern_break_rate(
        db,
        guard_present=guard_present,
        test_mocked=test_mocked,
        certainty=certainty,
        has_routes=has_routes,
        scenario=scenario,
        target_type=target_type,
    )

    if break_rate is not None:
        calibrated_score = round(break_rate * 100.0, 1)
        active_score = calibrated_score
        source = "calibrated"
        note = (
            f"Calibrated empirical score ({calibrated_score}) based on {sample_count} observed experiment outcomes "
            f"(observed break rate: {round(break_rate * 100, 1)}%, level: {level})."
        )
    else:
        calibrated_score = None
        active_score = heuristic_score
        source = "heuristic_fallback"
        note = "No empirical outcome data for pattern; falling back to heuristic formula."

    return {
        "score": active_score,
        "calibrated_score": calibrated_score,
        "heuristic_score": heuristic_score,
        "source": source,
        "break_rate": break_rate,
        "sample_count": sample_count,
        "level": level,
        "pattern": {
            "guard_present": guard_present,
            "test_mocked": test_mocked,
            "certainty": certainty,
            "routes_affected": routes_count,
            "has_routes": has_routes,
            "scenario": scenario,
            "target_type": target_type,
        },
        "note": note,
    }


def seed_calibration_outcomes(
    db: Session,
    guarded_break_rate: float = 0.10,
    unguarded_break_rate: float = 0.85,
    sample_size: int = 100,
    scenario: str = "external_api_unavailable",
    target_type: str = "external_api",
    project_id: str = "seed",
) -> dict[str, int]:
    """Helper to seed synthetic outcomes reflecting given break rates."""
    guarded_broke = int(sample_size * guarded_break_rate)
    unguarded_broke = int(sample_size * unguarded_break_rate)

    records = []
    # Seed guarded outcomes
    for i in range(sample_size):
        broke = i < guarded_broke
        records.append(
            PredictionOutcome(
                scenario=scenario,
                target_type=target_type,
                target_id=f"target:{target_type}",
                project_id=project_id,
                test_nodeid=f"test_guarded_{i}",
                guard_present=True,
                test_mocked=False,
                certainty="possible",
                routes_affected=1,
                predicted_outcome="may_fail",
                observed_outcome="failed" if broke else "passed",
                verdict="match" if broke else "match",
                broke=broke,
                pattern_key=build_pattern_key(True, False, "possible", True),
                created_at=datetime.now(timezone.utc),
            )
        )

    # Seed unguarded outcomes
    for i in range(sample_size):
        broke = i < unguarded_broke
        records.append(
            PredictionOutcome(
                scenario=scenario,
                target_type=target_type,
                target_id=f"target:{target_type}",
                project_id=project_id,
                test_nodeid=f"test_unguarded_{i}",
                guard_present=False,
                test_mocked=False,
                certainty="definite",
                routes_affected=1,
                predicted_outcome="fail",
                observed_outcome="failed" if broke else "passed",
                verdict="match" if broke else "mismatch",
                broke=broke,
                pattern_key=build_pattern_key(False, False, "definite", True),
                created_at=datetime.now(timezone.utc),
            )
        )

    db.add_all(records)
    db.commit()
    return {"guarded_seeded": sample_size, "unguarded_seeded": sample_size}

