"""PR intelligence: deterministic review package for a proposed change."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_project, require_run
from app.db import get_db
from app.models import Finding
from app.schemas import PrReviewIn
from app.services.analysis import load_graph
from app.services.pr_review import pr_review

router = APIRouter(prefix="/api", tags=["pr-review"])


@router.post("/projects/{project_id}/pr-review")
def pr_review_ep(project_id: str, body: PrReviewIn, db: Session = Depends(get_db)):
    """Deterministic review package for a PR's changed files: impact,
    test coverage, touching findings and the reliability memory excerpt.
    Read-only; no AI output; not an approval."""
    p = get_project(db, project_id)
    run = require_run(db, p)
    _, _, g = load_graph(db, run.id)
    rows = db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    findings = [{"id": f.id, "rule": f.rule, "title": f.title, "severity": f.severity,
                 "component_id": f.component_id, "evidence": f.evidence or [],
                 "confidence": f.confidence, "review_status": f.review_status} for f in rows]
    result = pr_review(db, g, body.files, findings, project_id=p.id, title=body.title)
    result["project"] = {"id": p.id, "name": p.name}
    result["run_id"] = run.id
    return result
