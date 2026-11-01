"""Reliability memory: the durable track record of how DevDNA's predictions
have fared against observed experiments.

Every completed experiment persists one PredictionOutcome row per compared
test (prediction, observed outcome, verdict, pattern features). This module
aggregates those rows into a queryable memory so users can see, per scenario
and per claim pattern, how often predictions were confirmed vs contradicted —
and how much data stands behind each rate.

Honesty rules:
- Memory only contains what experiments actually observed. Predictions that
  were never run are absent, and absence is reported as such.
- Any rate computed from fewer than MIN_SAMPLE_FOR_CALIBRATION observations
  is reported as `insufficient_data` instead of a fluke percentage.
- Verdicts of `excluded` (test failed at baseline / did not run) carry no
  evidence and are excluded from accuracy but reported by count.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PredictionOutcome
from app.services.calibration import MIN_SAMPLE_FOR_CALIBRATION


def _rate(numerator: int, denominator: int) -> dict:
    """A rate with an explicit sample size; insufficient below the threshold."""
    if denominator < MIN_SAMPLE_FOR_CALIBRATION:
        return {"status": "insufficient_data", "sample": denominator, "rate": None}
    return {"status": "measured", "sample": denominator, "rate": round(numerator / denominator, 3)}


def reliability_memory(db: Session, project_id: str | None = None) -> dict:
    q = select(PredictionOutcome)
    if project_id:
        q = q.where(PredictionOutcome.project_id == project_id)
    rows = db.scalars(q.order_by(PredictionOutcome.created_at)).all()

    scope = {"project_id": project_id} if project_id else {"project_id": None, "scope": "global"}
    if not rows:
        return {
            **scope,
            "status": "empty",
            "note": ("No experiment observations are recorded"
                     + (" for this project." if project_id else " yet.")
                     + " Run controlled experiments (POST /predictions/{id}/experiments) to "
                       "build a reliability memory. Nothing here is inferred or simulated."),
            "summary": {"observations": 0, "experiments": 0, "first_observed_at": None,
                        "last_observed_at": None},
            "by_scenario": [],
            "by_pattern": [],
            "method": ("Aggregates per-test PredictionOutcome rows persisted by completed "
                       "experiments (prediction vs observed outcome per test)."),
            "limitations": [
                "Memory covers only claims that were experimentally observed; unobserved "
                "predictions are absent and reported as such.",
                "Rates from fewer than "
                f"{MIN_SAMPLE_FOR_CALIBRATION} observations are reported as insufficient_data.",
                "A mismatch means the prediction was wrong for that test in that experiment; it "
                "does not by itself explain why (the comparison rows retain the details).",
            ],
        }

    experiment_ids = {r.experiment_id for r in rows if r.experiment_id}

    by_scenario: dict[str, dict] = {}
    for r in rows:
        s = by_scenario.setdefault(r.scenario, {
            "scenario": r.scenario, "observations": 0, "experiments": set(),
            "fail_predicted": 0, "fail_confirmed": 0,
            "pass_predicted": 0, "pass_violated": 0,
            "uncertain": 0, "excluded": 0, "broke": 0,
            "first": None, "last": None,
        })
        s["observations"] += 1
        if r.experiment_id:
            s["experiments"].add(r.experiment_id)
        if r.verdict == "excluded":
            s["excluded"] += 1
        elif r.predicted_outcome == "fail":
            s["fail_predicted"] += 1
            if r.verdict == "match":
                s["fail_confirmed"] += 1
        elif r.predicted_outcome == "pass":
            s["pass_predicted"] += 1
            if r.observed_outcome in ("failed", "error"):
                s["pass_violated"] += 1
        else:
            s["uncertain"] += 1
        if r.broke:
            s["broke"] += 1
        ts = r.created_at
        if s["first"] is None or ts < s["first"]:
            s["first"] = ts
        if s["last"] is None or ts > s["last"]:
            s["last"] = ts

    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    scenario_rows = []
    for name, s in sorted(by_scenario.items()):
        scenario_rows.append({
            "scenario": name,
            "observations": s["observations"],
            "experiments": len(s["experiments"]),
            "fail_prediction_accuracy": _rate(s["fail_confirmed"], s["fail_predicted"]),
            "pass_prediction_accuracy": _rate(s["pass_predicted"] - s["pass_violated"], s["pass_predicted"]),
            "uncertain_observations": s["uncertain"],
            "excluded_observations": s["excluded"],
            "tests_broke": s["broke"],
            "first_observed_at": _iso(s["first"]),
            "last_observed_at": _iso(s["last"]),
        })

    # pattern memory: the same claim patterns the calibration engine keys on
    by_pattern: dict[str, dict] = {}
    for r in rows:
        if r.verdict == "excluded":
            continue
        p = by_pattern.setdefault(r.pattern_key, {
            "pattern_key": r.pattern_key, "guard_present": bool(r.guard_present),
            "test_mocked": bool(r.test_mocked), "certainty": r.certainty,
            "has_route_path": r.routes_affected > 0,
            "observations": 0, "mismatches": 0,
        })
        p["observations"] += 1
        if r.verdict == "mismatch":
            p["mismatches"] += 1
    pattern_rows = []
    for key, p in sorted(by_pattern.items()):
        entry = {k: v for k, v in p.items() if k not in ("observations", "mismatches")}
        entry["observations"] = p["observations"]
        entry["contradiction_rate"] = _rate(p["mismatches"], p["observations"])
        pattern_rows.append(entry)

    return {
        **scope,
        "status": "populated",
        "summary": {
            "observations": len(rows),
            "experiments": len(experiment_ids),
            "scenarios": len(scenario_rows),
            "patterns": len(pattern_rows),
            "first_observed_at": _iso(rows[0].created_at),
            "last_observed_at": _iso(rows[-1].created_at),
            "min_sample_for_rates": MIN_SAMPLE_FOR_CALIBRATION,
        },
        "by_scenario": scenario_rows,
        "by_pattern": pattern_rows,
        "method": ("Aggregates per-test PredictionOutcome rows persisted by completed "
                   "experiments (prediction vs observed outcome per test)."),
        "limitations": [
            "Memory covers only claims that were experimentally observed; unobserved "
            "predictions are absent and reported as such.",
            "Rates from fewer than "
            f"{MIN_SAMPLE_FOR_CALIBRATION} observations are reported as insufficient_data.",
            "A mismatch means the prediction was wrong for that test in that experiment; it "
            "does not by itself explain why (the comparison rows retain the details).",
        ],
    }
