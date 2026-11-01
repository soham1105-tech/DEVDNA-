"""AgentGuard API: read-only security audit of a project's agent configs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from app.agentguard.service import audit_repository
from app.api.deps import get_project
from app.db import get_db
from app.services.serialize import iso

router = APIRouter(prefix="/api", tags=["agentguard"])


@router.get("/projects/{project_id}/agentguard")
def agentguard_audit(project_id: str, db = Depends(get_db)):
    """Deterministic security audit of the project's AI-agent configuration.

    Read-only: parses documented config formats (MCP server definitions,
    tool-permission settings, agent instruction files, env files, CI
    workflows, dependency manifests), builds a permission graph, and reports
    evidence-backed findings labelled suspected vs verified. Nothing is
    executed.
    """
    p = get_project(db, project_id)
    result = audit_repository(Path(p.source_path))
    result["project"] = {"id": p.id, "name": p.name, "generated_at": iso(datetime.now(timezone.utc))}
    return result
