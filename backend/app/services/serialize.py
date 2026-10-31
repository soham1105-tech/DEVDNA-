"""ORM -> JSON helpers. Source paths of local projects are never exposed."""
from __future__ import annotations

from datetime import timezone

from app.analyzer.redact import redact_obj
from app.models import AnalysisRun, Experiment, Finding, Prediction, Project
from app.services.finding_workflow import active_suppression, finding_fingerprint

STATUS_LABELS = {
    "prediction_only": "Prediction only",
    "running": "Running",
    "experiment_executed": "Experiment executed",
    "prediction_confirmed": "Prediction confirmed",
    "partially_confirmed": "Partially confirmed",
    "prediction_contradicted": "Prediction contradicted",
    "uncertain — covering tests mock the dependency": "Uncertain (Covering tests mock dependency)",
    "uncertain": "Uncertain",
    "inconclusive": "Inconclusive",
    "blocked": "Blocked",
    "failed": "Execution failed",
}


def iso(dt):
    """SQLite drops tzinfo; all stored times are UTC."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def project_out(p: Project, run: AnalysisRun | None = None) -> dict:
    return {
        "id": p.id, "name": p.name, "source_type": p.source_type, "trusted": p.trusted,
        "ast_repository": (
            {"owner": p.ast_owner, "repo": p.ast_repo, "branch": p.ast_branch}
            if p.ast_owner and p.ast_repo and p.ast_branch else None
        ),
        "ingest_stats": p.ingest_stats or {}, "created_at": iso(p.created_at), "latest_run_id": p.latest_run_id,
        "latest_run": run_out(run) if run else None,
    }


def run_out(r: AnalysisRun) -> dict:
    return {"id": r.id, "project_id": r.project_id, "status": r.status, "stage": r.stage, "progress": r.progress,
            "stages": r.stages or [], "summary": r.summary or {}, "error": r.error,
            "started_at": iso(r.started_at), "finished_at": iso(r.finished_at)}


def finding_out(f: Finding, suppression=None) -> dict:
    suppressed = active_suppression(suppression)
    return {"id": f.id, "run_id": f.run_id, "rule": f.rule, "title": f.title, "severity": f.severity,
            "component_id": f.component_id, "description": f.description, "evidence": redact_obj(f.evidence or []),
            "confidence": f.confidence, "assumptions": f.assumptions or [], "mitigation": f.mitigation,
            "review_status": f.review_status, "owner": f.owner or "Unassigned",
            "is_suppressed": suppressed,
            "suppression": {
                "owner": suppression.owner, "reason": suppression.reason, "expires_at": iso(suppression.expires_at),
                "active": suppressed,
            } if suppression else None,
            "fingerprint": finding_fingerprint(f), "created_at": iso(f.created_at)}


def prediction_out(p: Prediction) -> dict:
    return {"id": p.id, "project_id": p.project_id, "run_id": p.run_id, "scenario": p.scenario,
            "target_id": p.target_id, "impact": p.impact, "ai_explanation": redact_obj(p.ai_explanation),
            "created_at": iso(p.created_at), "kind": "prediction"}


def experiment_out(e: Experiment, include_prediction: bool = True) -> dict:
    out = {
        "id": e.id, "project_id": e.project_id, "prediction_id": e.prediction_id, "status": e.status,
        "status_label": STATUS_LABELS.get(e.status, e.status), "mode": e.mode,
        "approved_at": iso(e.approved_at), "started_at": iso(e.started_at), "finished_at": iso(e.finished_at),
        "observation": redact_obj(e.observation or {}), "comparison": redact_obj(e.comparison or {}), "ai_interpretation": redact_obj(e.ai_interpretation),
        "error": e.error, "created_at": iso(e.created_at),
    }
    if include_prediction and e.prediction:
        out["prediction"] = prediction_out(e.prediction)
    return out
