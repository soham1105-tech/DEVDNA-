from fastapi import HTTPException, Query
from sqlalchemy.orm import Session

from app.models import AnalysisRun, Project


def get_project(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, detail={"code": "project_not_found", "message": f"Project '{project_id}' not found"})
    return p


def require_run(db: Session, project: Project) -> AnalysisRun:
    if not project.latest_run_id:
        raise HTTPException(409, detail={"code": "not_analysed", "message": "This project has not been analysed yet."})
    return db.get(AnalysisRun, project.latest_run_id)


MAX_PAGE = 200


def pagination_params(limit: int = Query(50, ge=0), offset: int = Query(0, ge=0)) -> tuple[int, int]:
    """Shared limit/offset for list endpoints.

    limit defaults to 50 and is clamped to 200; negative or non-integer values
    are rejected with 422 by the Query validation itself.
    """
    return min(limit, MAX_PAGE), offset
