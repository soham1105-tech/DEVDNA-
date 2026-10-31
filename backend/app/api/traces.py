"""Runtime trace upload: validate a production trace against the static graph.

POST /api/runs/{run_id}/traces accepts a JSON body or a multipart file with an
OpenTelemetry export or a simple caller->callee edge list, compares it to the
run's stored graph (see services/traces.py) and persists the differences as
findings. Re-uploading replaces the previous trace findings for that run.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AnalysisRun, Finding
from app.services.analysis import component_dicts, edge_dicts
from app.services.serialize import finding_out
from app.services.traces import TraceValidationError, compare_traces, parse_trace_bytes, trace_findings

log = logging.getLogger("devdna.traces")
router = APIRouter(prefix="/api", tags=["traces"])

TRACE_RULES = ("static_edge_unobserved", "runtime_edge_missing_from_model")


async def _read_trace_payload(request: Request) -> bytes:
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(422, detail={"code": "trace_file_missing",
                                             "message": "Multipart uploads need a 'file' field."})
        payload = await upload.read()
        if hasattr(upload, "filename") and upload.filename and not upload.filename.endswith(".json"):
            raise HTTPException(422, detail={"code": "trace_file_invalid",
                                             "message": "Trace files must be JSON (OpenTelemetry export or edge list)."})
        return payload
    return await request.body()


@router.post("/runs/{run_id}/traces", status_code=201)
async def upload_trace(run_id: str, request: Request, db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, run_id)
    if not run:
        raise HTTPException(404, detail={"code": "run_not_found", "message": f"Run '{run_id}' not found"})
    if run.status != "completed":
        raise HTTPException(409, detail={"code": "run_not_completed",
                                         "message": "Traces can only be compared against a completed analysis."})

    payload = await _read_trace_payload(request)
    if len(payload) > settings.max_trace_bytes:
        raise HTTPException(413, detail={"code": "trace_too_large",
                                         "message": f"Trace exceeds the {settings.max_trace_bytes // (1024 * 1024)} MB limit"})
    try:
        trace = parse_trace_bytes(payload)
    except TraceValidationError as exc:
        raise HTTPException(422, detail={"code": "trace_invalid", "message": str(exc)}) from exc

    components = component_dicts(db, run.id)
    edges = edge_dicts(db, run.id)
    comparison = compare_traces(components, edges, trace)
    findings = trace_findings(comparison, components)

    # re-upload replaces the previous trace findings for this run
    db.execute(delete(Finding).where(Finding.run_id == run.id, Finding.rule.in_(TRACE_RULES)))
    rows = [Finding(run_id=run.id, project_id=run.project_id, **f) for f in findings]
    for row in rows:
        db.add(row)
    db.commit()
    log.info("Trace comparison for run %s: %d/%d static edges observed, %d runtime-only, %d unmapped",
             run.id, comparison["observed_static_edges"], comparison["static_comparable_edges"],
             len(comparison["runtime_only_edges"]), comparison["unmapped_count"])
    return {
        "trace": {"format": comparison["trace_format"], "edges": comparison["trace_edges"],
                  "spans": comparison["trace_spans"]},
        "comparison": comparison,
        "findings": [finding_out(r) for r in rows],
    }
