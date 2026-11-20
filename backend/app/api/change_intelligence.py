"""DevDNA API - Change Intelligence Endpoints.

Provides:
- Starting a change analysis (diff, branch, or file list)
- Retrieving analysis results, graph diff, risk score, prioritized tests, and merge recommendation
- Running / re-running controlled validation experiments
- Built-in sample scenario (database connection handler modification)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_project, pagination_params
from app.db import get_db
from app.models import ChangeAnalysis, Project
from app.schemas import ChangeIntelligenceIn
from app.services.change_intelligence import analyze_code_change
from app.services.change_validation import run_controlled_validation
from app.services.merge_recommendation import generate_merge_recommendation
from app.services.serialize import iso

router = APIRouter(prefix="/api", tags=["change intelligence"])
log = logging.getLogger("devdna.api.change_intelligence")


def _change_analysis_out(rec: ChangeAnalysis) -> dict[str, Any]:
    return {
        "id": rec.id,
        "project_id": rec.project_id,
        "title": rec.title,
        "base_ref": rec.base_ref,
        "head_ref": rec.head_ref,
        "status": rec.status,
        "created_at": iso(rec.created_at),
        "diff_manifest": rec.diff_manifest,
        "graph_diff": rec.graph_diff,
        "risk_assessment": rec.risk_assessment,
        "prioritized_tests": rec.prioritized_tests,
        "validation": rec.validation,
        "recommendation": rec.recommendation,
        "ai_explanation": rec.ai_explanation,
        "error": rec.error,
    }


@router.post("/projects/{project_id}/change-intelligence/analyze", status_code=200)
def analyze_change(
    project_id: str,
    payload: ChangeIntelligenceIn,
    db: Session = Depends(get_db),
):
    """Analyze a proposed code change against the base repository dependency graph."""
    get_project(db, project_id)
    try:
        record = analyze_code_change(
            db,
            project_id,
            files=payload.files,
            diff_text=payload.diff_text,
            base_ref=payload.base_ref,
            head_ref=payload.head_ref,
            run_validation=payload.run_validation,
            title=payload.title,
            request_ai=payload.request_ai,
        )
        return _change_analysis_out(record)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "analysis_error", "message": str(e)}) from e
    except Exception as e:
        log.error("Change intelligence analysis failed: %s", e, exc_info=True)
        raise HTTPException(500, detail={"code": "internal_error", "message": f"Change intelligence failed: {e}"}) from e


@router.get("/projects/{project_id}/change-intelligence", status_code=200)
def list_change_analyses(
    project_id: str,
    params: tuple[int, int] = Depends(pagination_params),
    db: Session = Depends(get_db),
):
    """List historical Change Intelligence analyses for the project."""
    get_project(db, project_id)
    limit, offset = params
    total = db.scalar(select(func.count()).select_from(ChangeAnalysis).where(ChangeAnalysis.project_id == project_id)) or 0
    rows = db.scalars(
        select(ChangeAnalysis)
        .where(ChangeAnalysis.project_id == project_id)
        .order_by(ChangeAnalysis.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = [_change_analysis_out(r) for r in rows]
    return JSONResponse(content=items, headers={"X-Total-Count": str(total)})


@router.get("/projects/{project_id}/change-intelligence/{analysis_id}", status_code=200)
def get_change_analysis(
    project_id: str,
    analysis_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve full results of a specific Change Intelligence analysis."""
    get_project(db, project_id)
    rec = db.get(ChangeAnalysis, analysis_id)
    if not rec or rec.project_id != project_id:
        raise HTTPException(404, detail={"code": "not_found", "message": "Change analysis not found."})
    return _change_analysis_out(rec)


@router.post("/projects/{project_id}/change-intelligence/{analysis_id}/validate", status_code=200)
def trigger_validation(
    project_id: str,
    analysis_id: str,
    db: Session = Depends(get_db),
):
    """Execute or re-run controlled empirical validation for an existing change analysis."""
    project = get_project(db, project_id)
    rec = db.get(ChangeAnalysis, analysis_id)
    if not rec or rec.project_id != project_id:
        raise HTTPException(404, detail={"code": "not_found", "message": "Change analysis not found."})

    val_res = run_controlled_validation(
        project,
        rec.graph_diff,
        rec.prioritized_tests.get("tests", []),
    )
    rec.validation = val_res

    # Re-evaluate recommendation with new validation evidence
    rec.recommendation = generate_merge_recommendation(
        rec.risk_assessment,
        rec.graph_diff,
        rec.prioritized_tests,
        [],
        val_res,
    )
    db.commit()
    db.refresh(rec)
    return _change_analysis_out(rec)


DEMO_DIFF = """diff --git a/shoplite/db.py b/shoplite/db.py
--- a/shoplite/db.py
+++ b/shoplite/db.py
@@ -14,6 +14,8 @@
 def get_db():
-    db = SessionLocal()
+    # Modified database connection pool handler with strict serializable isolation
+    db = SessionLocal(expire_on_commit=False, isolation_level="SERIALIZABLE")
     try:
         yield db
     finally:
"""


@router.post("/projects/{project_id}/change-intelligence/sample-scenario", status_code=200)
@router.post("/projects/{project_id}/change-intelligence/demo", status_code=200)
def run_sample_scenario(
    project_id: str,
    db: Session = Depends(get_db),
):
    """Execute the bundled Change Intelligence sample scenario on the ShopLite reference project.

    Scenario: A developer modifies the database connection handler in shoplite/db.py:get_db.
    DevDNA detects:
    1. Changed function: get_db in shoplite/db.py
    2. Dependent services and API routes (pricing, orders, /api/orders, /api/checkout)
    3. Multi-factor risk calculation & blast radius
    4. Prioritized tests (test_api.py::test_create_order, etc.)
    5. Controlled database-unavailable validation (when experiments are permitted)
    6. Prediction vs reality comparison
    7. Evidence-backed merge recommendation with grounded reasoning.
    """
    project = get_project(db, project_id)
    if project.source_type != "sample":
        raise HTTPException(
            400,
            detail={
                "code": "sample_only_scenario",
                "message": (
                    "The bundled ShopLite DB Handler demonstration scenario can only run on the "
                    "ShopLite sample project. For imported repositories, provide changed file paths "
                    "from that repository or paste a unified git diff."
                ),
            },
        )
    try:
        record = analyze_code_change(
            db,
            project_id,
            diff_text=DEMO_DIFF,
            run_validation=True,
            title="Demo Scenario (ShopLite): Update Database Session Isolation Handler",
            request_ai=True,
        )
        manifest = dict(record.diff_manifest or {})
        manifest["is_demo"] = True
        record.diff_manifest = manifest
        db.commit()
        db.refresh(record)
        return _change_analysis_out(record)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "sample_scenario_error", "message": str(e)}) from e
    except Exception as e:
        log.error("Change intelligence sample scenario failed: %s", e, exc_info=True)
        raise HTTPException(500, detail={"code": "sample_scenario_failed", "message": f"Sample scenario failed: {e}"}) from e
