"""DevDNA Change Intelligence - Master Orchestration Service.

Coordinates:
1. Change Detection (Git diff / PR / commits / files)
2. Before-and-After Graph Dependency Analysis
3. Explainable Change Risk Scoring
4. 5-Tiered Intelligent Test Prioritisation
5. Controlled Validation via DevDNA Experiment Harness
6. Evidence-Backed Merge Recommendation
7. Gemini Grounded Reasoning Layer
8. Persistence & Audit Logging
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.change_explainer import explain_change
from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph
from app.models import AnalysisRun, ChangeAnalysis, Finding, Project
from app.services.analysis import load_graph
from app.services.change_detection import (
    FileChange,
    build_change_manifest,
    detect_git_changes,
    enrich_file_changes_with_ast,
    parse_unified_diff,
)
from app.services.change_risk import calculate_change_risk
from app.services.change_validation import run_controlled_validation
from app.services.graph_diff import diff_dependency_graphs
from app.services.merge_recommendation import generate_merge_recommendation
from app.services.test_prioritization import prioritize_tests

log = logging.getLogger("devdna.change_intelligence")


def analyze_code_change(
    db: Session,
    project_id: str,
    *,
    files: list[str] | None = None,
    diff_text: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    run_validation: bool = False,
    title: str | None = None,
    request_ai: bool = True,
) -> ChangeAnalysis:
    """Run end-to-end Change Intelligence on a proposed code change."""
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"Project '{project_id}' not found.")
    if not project.latest_run_id:
        raise ValueError(f"Project '{project_id}' has no completed analysis run. Please analyze the project first.")

    source_path = Path(project.source_path)

    # 1. Load base graph
    _comps, _edges, base_graph = load_graph(db, project.latest_run_id)

    # 2. Detect changes
    file_changes: list[FileChange] = []
    if diff_text and diff_text.strip():
        file_changes = parse_unified_diff(diff_text)
        enrich_file_changes_with_ast(file_changes, base_root=source_path, head_root=source_path)
    elif (source_path / ".git").exists() and (base_ref or not files):
        file_changes, _ = detect_git_changes(source_path, base_ref=base_ref, head_ref=head_ref)
    elif files:
        for f in files:
            file_changes.append(FileChange(path=f, status="modified"))
        enrich_file_changes_with_ast(file_changes, base_root=source_path, head_root=source_path)
    else:
        # Default: if no files or diff passed, inspect git status or assume no-op
        if (source_path / ".git").exists():
            file_changes, _ = detect_git_changes(source_path)

    change_manifest = build_change_manifest(file_changes)
    changed_file_paths = [f["path"] for f in change_manifest["files"]]
    changed_components = [
        c
        for f in change_manifest["files"]
        for c in f.get("changed_components", [])
    ]

    # 3. Before-and-after graph comparison
    # We use base_graph as reference; mod_graph includes any newly scanned components or graph nodes
    mod_graph = base_graph.copy()

    # Re-evaluate any new or modified components into mod_graph
    graph_diff = diff_dependency_graphs(
        base_graph=base_graph,
        mod_graph=mod_graph,
        changed_components=changed_components,
        changed_files=changed_file_paths,
    )

    # 4. Fetch findings on the latest run
    findings_rows = db.scalars(select(Finding).where(Finding.run_id == project.latest_run_id)).all()
    findings_list = [
        {
            "id": f.id,
            "rule": f.rule,
            "title": f.title,
            "severity": f.severity,
            "evidence": f.evidence,
        }
        for f in findings_rows
    ]

    # 5. Calculate Change Risk
    total_routes = sum(1 for _, d in base_graph.nodes(data=True) if d.get("type") == "route")
    unmapped_files = [
        {"file": f["path"], "reason": "Not mapped onto analysed component graph"}
        for f in change_manifest["files"]
        if not any(
            (d.get("file") or "").replace("\\", "/").lstrip("./").lstrip("/") == f["path"].replace("\\", "/").lstrip("./").lstrip("/")
            for _, d in base_graph.nodes(data=True)
        )
    ]

    risk_assessment = calculate_change_risk(
        db=db,
        project_id=project_id,
        graph_diff=graph_diff,
        findings=findings_list,
        unmapped_files=unmapped_files,
        total_routes_in_repo=total_routes,
    )

    # 6. Prioritize Tests
    prioritized_tests = prioritize_tests(mod_graph, graph_diff)

    # 7. Controlled Validation (run if explicitly requested or if high-confidence demo project)
    validation_result: dict[str, Any] | None = None
    if run_validation:
        validation_result = run_controlled_validation(
            project=project,
            graph_diff=graph_diff,
            prioritized_tests=prioritized_tests["tests"],
        )

    # 8. Merge Recommendation
    recommendation = generate_merge_recommendation(
        risk=risk_assessment,
        graph_diff=graph_diff,
        prioritized_tests=prioritized_tests,
        findings=findings_list,
        validation=validation_result,
    )

    # 9. Gemini Reasoning Layer
    ai_explanation: dict[str, Any] | None = None
    if request_ai:
        ai_explanation = explain_change(
            change_manifest=change_manifest,
            graph_diff=graph_diff,
            risk=risk_assessment,
            prioritized_tests=prioritized_tests,
            validation=validation_result,
            recommendation=recommendation,
        )

    # 10. Persist ChangeAnalysis record
    run_title = title or (
        f"PR: Change {len(changed_file_paths)} file(s)"
        if changed_file_paths
        else "Change Intelligence Analysis"
    )

    change_record = ChangeAnalysis(
        project_id=project.id,
        title=run_title,
        base_ref=base_ref,
        head_ref=head_ref,
        status="completed",
        diff_manifest=change_manifest,
        graph_diff=graph_diff,
        risk_assessment=risk_assessment,
        prioritized_tests=prioritized_tests,
        validation=validation_result,
        recommendation=recommendation,
        ai_explanation=ai_explanation,
    )

    db.add(change_record)
    db.commit()
    db.refresh(change_record)

    return change_record
