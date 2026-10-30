"""Runs analysis in the background and persists results; loads graphs for later requests."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph, compute_metrics
from app.models import AnalysisRun, Component, Edge, Finding, Project
from app.services.findings import generate_findings
from app.services.ownership import assign_finding_owners

log = logging.getLogger("devdna.analysis")
_graph_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


def run_analysis(run_id: str) -> None:
    db = dbmod.SessionLocal()
    try:
        run = db.get(AnalysisRun, run_id)
        project = db.get(Project, run.project_id)
        run.status, run.stage, run.progress = "running", "Starting", 0.01
        run.stages = []
        db.commit()

        def progress(stage: str, frac: float):
            run.stage, run.progress = stage, frac
            run.stages = [*run.stages, {"stage": stage, "at": datetime.now(timezone.utc).isoformat()}]
            db.commit()

        result = analyse_repository(Path(project.source_path), progress)
        progress("Computing graph metrics", 0.9)
        g = build_graph(result["components"], result["edges"])
        metrics = compute_metrics(g)
        progress("Generating findings", 0.95)
        findings = generate_findings(g, result)
        assign_finding_owners(Path(project.source_path), findings, result["components"])

        for c in result["components"]:
            db.add(Component(run_id=run.id, id=c["id"], type=c["type"], name=c["name"], file=c["file"], line=c["line"],
                             data=c["data"], metrics=metrics.get(c["id"], {})))
        for e in result["edges"]:
            db.add(Edge(run_id=run.id, id=e["id"], source=e["source"], target=e["target"], type=e["type"],
                        classification=e["classification"], evidence=e["evidence"], call_sites=e["call_sites"]))
        for f in findings:
            db.add(Finding(run_id=run.id, project_id=project.id, **f))
        counts: dict[str, int] = {}
        for c in result["components"]:
            counts[c["type"]] = counts.get(c["type"], 0) + 1
        edge_counts: dict[str, int] = {}
        for e in result["edges"]:
            edge_counts[e["type"]] = edge_counts.get(e["type"], 0) + 1
        run.summary = {
            "component_counts": counts,
            "edge_counts": edge_counts,
            "edge_classification": {k: sum(1 for e in result["edges"] if e["classification"] == k)
                                    for k in ("confirmed", "inferred", "unknown")},
            "scan": result["scan"],
            "frameworks": result["frameworks"],
            "third_party_imports": result["third_party_imports"],
            "dependencies": result["dependencies"],
            "coverage": result["coverage"],
            "findings": len(findings),
        }
        if g.graph.get("warnings"):
            run.summary["graph_warnings"] = g.graph["warnings"]
        run.status, run.stage, run.progress = "completed", "Completed", 1.0
        run.finished_at = datetime.now(timezone.utc)
        project.latest_run_id = run.id
        db.commit()
    except Exception as exc:  # report the failure honestly
        log.exception("analysis failed")
        db.rollback()
        run = db.get(AnalysisRun, run_id)
        if run:
            run.status, run.stage = "failed", "Failed"
            run.error = f"{type(exc).__name__}: {exc}"[:1000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def component_dicts(db: Session, run_id: str) -> list[dict]:
    rows = db.scalars(select(Component).where(Component.run_id == run_id)).all()
    return [{"id": c.id, "type": c.type, "name": c.name, "file": c.file, "line": c.line, "data": c.data or {},
             "metrics": c.metrics or {}} for c in rows]


def edge_dicts(db: Session, run_id: str) -> list[dict]:
    rows = db.scalars(select(Edge).where(Edge.run_id == run_id)).all()
    return [{"id": e.id, "source": e.source, "target": e.target, "type": e.type, "classification": e.classification,
             "evidence": e.evidence or [], "call_sites": e.call_sites or []} for e in rows]


def load_graph(db: Session, run_id: str):
    """Return (components, edges, graph) for a completed run (cached; runs are immutable)."""
    with _cache_lock:
        if run_id in _graph_cache:
            return _graph_cache[run_id]
    comps = component_dicts(db, run_id)
    edges = edge_dicts(db, run_id)
    g = build_graph(comps, edges)
    with _cache_lock:
        _graph_cache[run_id] = (comps, edges, g)
    return comps, edges, g
