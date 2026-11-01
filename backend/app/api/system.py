"""Health, AI status and report downloads."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.gemma import gemma
from app.api.deps import get_project
from app.db import get_db
from app.experiments.runner import docker_available
from app.models import Experiment, ReportRecord
from app.services import reports
from app.services.serialize import iso

router = APIRouter(prefix="/api", tags=["system"])
VERSION = "0.1.0"


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        # fail closed for readiness probes: a load balancer must not route
        # traffic to an instance that cannot reach its database
        return JSONResponse(status_code=503,
                            content={"status": "degraded", "version": VERSION, "database": "error",
                                     "ai": {"configured": gemma.configured, "model": gemma.model}})
    return {"status": "ok", "version": VERSION, "database": database,
            "ai": {"configured": gemma.configured, "model": gemma.model}}


@router.get("/ai/status")
def ai_status(check: bool = False):
    """AI configuration. With ?check=true, asks the Gemini API whether the configured model is accessible."""
    if check and gemma.configured:
        gemma.check_model()
    return {**gemma.status(), "docker_sandbox": docker_available()}


def _download(db: Session, project_id: str, kind: str, subject_id: str, fmt: str, body: str, filename: str):
    data = body.encode("utf-8")
    db.add(ReportRecord(project_id=project_id, kind=kind, subject_id=subject_id, format=fmt, size_bytes=len(data)))
    db.commit()
    media = "application/json" if fmt == "json" else "text/markdown"
    return Response(content=data, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{project_id}/report")
def project_report(project_id: str, format: Literal["md", "json"] = Query("md"), db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    if not p.latest_run_id:
        raise HTTPException(409, detail={"code": "not_analysed", "message": "Analyse the project before generating a report."})
    r = reports.project_report(db, p)
    body = reports.to_json(r) if format == "json" else reports.project_markdown(r)
    return _download(db, p.id, "project", p.id, format, body, f"devdna-{p.id}-report.{format}")


@router.get("/experiments/{experiment_id}/report")
def experiment_report(experiment_id: str, format: Literal["md", "json"] = Query("md"), db: Session = Depends(get_db)):
    e = db.get(Experiment, experiment_id)
    if not e:
        raise HTTPException(404, detail={"code": "experiment_not_found", "message": "Experiment not found"})
    r = reports.experiment_report(e)
    body = reports.to_json(r) if format == "json" else reports.experiment_markdown(r)
    return _download(db, e.project_id, "experiment", e.id, format, body, f"devdna-experiment-{e.id}.{format}")


@router.get("/projects/{project_id}/reports")
def report_history(project_id: str, db: Session = Depends(get_db)):
    get_project(db, project_id)
    rows = db.scalars(select(ReportRecord).where(ReportRecord.project_id == project_id)
                      .order_by(ReportRecord.created_at.desc()).limit(50)).all()
    return [{"id": r.id, "kind": r.kind, "subject_id": r.subject_id, "format": r.format, "size_bytes": r.size_bytes,
             "created_at": iso(r.created_at)} for r in rows]
