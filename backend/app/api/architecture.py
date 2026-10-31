"""Architecture graph, component details, findings, scenarios and analytics."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.evidence import source_excerpt
from app.api.deps import get_project, require_run, pagination_params
from app.db import get_db
from app.graph.engine import PROPAGATING, SCENARIOS, neighbourhood
from app.models import AIInteraction, AnalysisRun, Component, Experiment, Finding, FindingSuppression, FindingSuppressionAudit, Prediction
from app.schemas import FindingPatch, FindingSuppressionIn
from app.services.finding_workflow import active_suppression, finding_fingerprint, suppression_map, suppression_for
from app.services.analysis import load_graph
from app.services.serialize import finding_out, iso

router = APIRouter(prefix="/api", tags=["architecture"])


@router.get("/projects/{project_id}/architecture")
def architecture(project_id: str, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    run = require_run(db, p)
    comps, edges, _ = load_graph(db, run.id)
    return {
        "run_id": run.id,
        "components": comps,
        "edges": [{k: e[k] for k in ("id", "source", "target", "type", "classification", "evidence")} for e in edges],
        "propagating_edge_types": sorted(PROPAGATING),
        "summary": run.summary,
    }


@router.get("/projects/{project_id}/components/{component_id:path}")
def component_detail(project_id: str, component_id: str, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    run = require_run(db, p)
    comps, edges, g = load_graph(db, run.id)
    comp = next((c for c in comps if c["id"] == component_id), None)
    if not comp:
        raise HTTPException(404, detail={"code": "component_not_found", "message": f"Component '{component_id}' not found"})
    strip = lambda e: {k: e[k] for k in ("id", "source", "target", "type", "classification", "evidence")}
    hood = neighbourhood(g, component_id)
    findings = db.scalars(select(Finding).where(Finding.run_id == run.id, Finding.component_id == component_id)).all()
    suppressions = suppression_map(db, findings)
    excerpt = source_excerpt(Path(p.source_path), comp.get("file"), comp.get("line")) \
        if comp["type"] in ("function", "class", "route", "app", "worker") else None
    return {
        **comp,
        "outgoing": [strip(e) for e in edges if e["source"] == component_id],
        "incoming": [strip(e) for e in edges if e["target"] == component_id],
        "upstream": hood["upstream"],
        "downstream": hood["downstream"],
        "findings": [finding_out(f, suppressions.get(finding_fingerprint(f))) for f in findings],
        "source_excerpt": excerpt,
    }


@router.get("/projects/{project_id}/findings")
def findings(project_id: str, db: Session = Depends(get_db), params: tuple[int, int] = Depends(pagination_params)):
    limit, offset = params
    p = get_project(db, project_id)
    if not p.latest_run_id:
        return []
    rows = db.scalars(select(Finding).where(Finding.run_id == p.latest_run_id).offset(offset).limit(limit)).all()
    suppressions = suppression_map(db, rows)
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted([finding_out(f, suppressions.get(finding_fingerprint(f))) for f in rows],
                  key=lambda f: (order.get(f["severity"], 9), f["title"]))


@router.patch("/findings/{finding_id}")
def update_finding(finding_id: str, body: FindingPatch, db: Session = Depends(get_db)):
    f = db.get(Finding, finding_id)
    if not f:
        raise HTTPException(404, detail={"code": "finding_not_found", "message": "Finding not found"})
    f.review_status = body.review_status
    db.commit()
    return finding_out(f, suppression_for(db, f))


@router.post("/findings/{finding_id}/suppress")
def suppress_finding(finding_id: str, body: FindingSuppressionIn, db: Session = Depends(get_db)):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, detail={"code": "finding_not_found", "message": "Finding not found"})
    reason, owner = body.reason.strip(), body.owner.strip()
    if not reason or not owner:
        raise HTTPException(422, detail={"code": "invalid_suppression",
                                         "message": "A non-empty reason and owner are required."})
    expires_at = body.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(422, detail={"code": "invalid_suppression_expiry",
                                         "message": "The suppression expiry must be in the future."})

    fingerprint = finding_fingerprint(finding)
    suppression = db.scalar(select(FindingSuppression).where(
        FindingSuppression.project_id == finding.project_id,
        FindingSuppression.fingerprint == fingerprint,
    ))
    if suppression is None:
        suppression = FindingSuppression(project_id=finding.project_id, fingerprint=fingerprint,
                                         finding_id=finding.id, owner=owner, reason=reason,
                                         expires_at=expires_at, active=True)
        db.add(suppression)
    else:
        suppression.finding_id = finding.id
        suppression.owner = owner
        suppression.reason = reason
        suppression.expires_at = expires_at
        suppression.active = True
    db.add(FindingSuppressionAudit(project_id=finding.project_id, fingerprint=fingerprint,
                                   finding_id=finding.id, action="suppressed", owner=owner,
                                   reason=reason, expires_at=expires_at))
    db.commit()
    return finding_out(finding, suppression)


@router.post("/findings/{finding_id}/unsuppress")
def unsuppress_finding(finding_id: str, db: Session = Depends(get_db)):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, detail={"code": "finding_not_found", "message": "Finding not found"})
    fingerprint = finding_fingerprint(finding)
    suppression = suppression_for(db, finding)
    if suppression is None:
        raise HTTPException(409, detail={"code": "finding_not_suppressed",
                                         "message": "This finding has no suppression to remove."})
    suppression.active = False
    suppression.finding_id = finding.id
    db.add(FindingSuppressionAudit(project_id=finding.project_id, fingerprint=fingerprint,
                                   finding_id=finding.id, action="unsuppressed",
                                   owner=suppression.owner, reason=suppression.reason,
                                   expires_at=suppression.expires_at))
    db.commit()
    return finding_out(finding, suppression)


@router.get("/findings/{finding_id}/suppression-audit")
def finding_suppression_audit(finding_id: str, db: Session = Depends(get_db)):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, detail={"code": "finding_not_found", "message": "Finding not found"})
    rows = db.scalars(select(FindingSuppressionAudit).where(
        FindingSuppressionAudit.project_id == finding.project_id,
        FindingSuppressionAudit.fingerprint == finding_fingerprint(finding),
    ).order_by(FindingSuppressionAudit.occurred_at.desc())).all()
    return [{"action": row.action, "owner": row.owner, "reason": row.reason,
             "expires_at": iso(row.expires_at), "occurred_at": iso(row.occurred_at)} for row in rows]


@router.get("/projects/{project_id}/drift")
def project_drift(project_id: str, db: Session = Depends(get_db)):
    project = get_project(db, project_id)
    latest = require_run(db, project)
    runs = db.scalars(select(AnalysisRun).where(
        AnalysisRun.project_id == project.id, AnalysisRun.status == "completed",
    ).order_by(AnalysisRun.finished_at.desc())).all()
    previous = next((run for run in runs if run.id != latest.id), None)
    if previous is None:
        return {"latest_run_id": latest.id, "previous_run_id": None, "has_comparison": False,
                "routes_added": [], "routes_removed": [], "blast_radius_growth": []}

    latest_components = db.scalars(select(Component).where(Component.run_id == latest.id)).all()
    previous_components = db.scalars(select(Component).where(Component.run_id == previous.id)).all()
    latest_routes = {component.id: component for component in latest_components if component.type == "route"}
    previous_routes = {component.id: component for component in previous_components if component.type == "route"}
    route_out = lambda component: {"id": component.id, "name": component.name, "file": component.file}
    latest_by_id = {component.id: component for component in latest_components if component.type != "test"}
    previous_by_id = {component.id: component for component in previous_components if component.type != "test"}
    growth = []
    for component_id in latest_by_id.keys() & previous_by_id.keys():
        old = previous_by_id[component_id].metrics or {}
        new = latest_by_id[component_id].metrics or {}
        old_radius = old.get("dependents", 0)
        new_radius = new.get("dependents", 0)
        if new_radius > old_radius:
            component = latest_by_id[component_id]
            growth.append({"id": component.id, "name": component.name, "type": component.type,
                           "file": component.file, "previous_blast_radius": old_radius,
                           "blast_radius": new_radius, "growth": new_radius - old_radius})
    growth.sort(key=lambda item: (-item["growth"], item["id"]))
    return {
        "latest_run_id": latest.id, "previous_run_id": previous.id, "has_comparison": True,
        "routes_added": [route_out(latest_routes[key]) for key in sorted(latest_routes.keys() - previous_routes.keys())],
        "routes_removed": [route_out(previous_routes[key]) for key in sorted(previous_routes.keys() - latest_routes.keys())],
        "blast_radius_growth": growth[:10],
    }


@router.get("/projects/{project_id}/scenarios")
def scenarios(project_id: str, db: Session = Depends(get_db)):
    """Supported failure scenarios and the discovered components each can target."""
    p = get_project(db, project_id)
    run = require_run(db, p)
    comps, _, _ = load_graph(db, run.id)
    out = []
    for key, spec in SCENARIOS.items():
        targets = [{"id": c["id"], "name": c["name"], "type": c["type"], "criticality": c["metrics"].get("criticality"),
                    "dependents": c["metrics"].get("dependents")}
                   for c in comps if c["type"] in spec["target_types"]]
        out.append({"id": key, "label": spec["label"], "fault": spec["fault"], "target_types": spec["target_types"],
                    "targets": sorted(targets, key=lambda t: -(t["criticality"] or 0)), "supported": bool(targets)})
    return out


@router.get("/projects/{project_id}/analytics")
def analytics(project_id: str, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    run = require_run(db, p)
    comps, edges, _ = load_graph(db, run.id)
    exps = db.scalars(select(Experiment).where(Experiment.project_id == p.id)).all()
    preds = db.scalars(select(Prediction).where(Prediction.project_id == p.id)).all()
    fnds = db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    suppressions = suppression_map(db, fnds)
    ai = db.scalars(select(AIInteraction).where(AIInteraction.project_id == p.id)).all()
    non_test = [c for c in comps if c["type"] not in ("test", "module")]
    centrality = sorted(non_test, key=lambda c: -c["metrics"].get("criticality", 0))[:10]
    outcome_counts: dict[str, int] = {}
    for e in exps:
        outcome_counts[e.status] = outcome_counts.get(e.status, 0) + 1
    severity_counts: dict[str, int] = {}
    for f in fnds:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
    latest_pred = max(preds, key=lambda x: x.created_at) if preds else None
    return {
        "component_counts": run.summary.get("component_counts", {}),
        "edge_counts": run.summary.get("edge_counts", {}),
        "edge_classification": run.summary.get("edge_classification", {}),
        "coverage": run.summary.get("coverage", {}),
        "scan": run.summary.get("scan", {}),
        "frameworks": run.summary.get("frameworks", []),
        "dependencies": run.summary.get("dependencies", []),
        "centrality": [{"id": c["id"], "name": c["name"], "type": c["type"], **c["metrics"]} for c in centrality],
        "experiments": {"total": len(exps), "by_status": outcome_counts},
        "predictions": {"total": len(preds),
                        "latest": {"id": latest_pred.id, "scenario": latest_pred.scenario, "target": latest_pred.target_id,
                                   "summary": latest_pred.impact.get("summary")} if latest_pred else None},
        "findings": {"total": len(fnds), "by_severity": severity_counts,
                     "open": sum(1 for f in fnds if f.review_status == "open"),
                     "suppressed": sum(1 for f in fnds if active_suppression(suppressions.get(finding_fingerprint(f)))),
                     "recent": [finding_out(f, suppressions.get(finding_fingerprint(f))) for f in
                                sorted(fnds, key=lambda f: ({"high": 0, "medium": 1, "low": 2}.get(f.severity, 3)))[:5]]},
        "ai": {"requests": len(ai), "ok": sum(1 for a in ai if a.status == "ok"), "errors": sum(1 for a in ai if a.status != "ok")},
    }
