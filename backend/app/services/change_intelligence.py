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

    # 4. Fetch findings on the latest run and separate change-specific vs repository-wide
    findings_rows = db.scalars(select(Finding).where(Finding.run_id == project.latest_run_id)).all()
    affected_comp_ids = (
        {c["id"] for c in graph_diff.get("directly_affected", [])}
        | {c["id"] for c in graph_diff.get("transitively_affected", [])}
        | {c["id"] for c in graph_diff.get("potentially_affected", [])}
        | {r["id"] for r in graph_diff.get("impacted_routes", [])}
    )
    affected_files = (
        {p.replace("\\", "/").lstrip("./").lstrip("/") for p in changed_file_paths}
        | {
            (c.get("file") or "").replace("\\", "/").lstrip("./").lstrip("/")
            for c in (graph_diff.get("directly_affected", []) + graph_diff.get("transitively_affected", []))
            if c.get("file")
        }
    )

    change_findings: list[dict[str, Any]] = []
    repository_findings: list[dict[str, Any]] = []
    for f in findings_rows:
        item = {
            "id": f.id,
            "rule": f.rule,
            "title": f.title,
            "severity": f.severity,
            "component_id": f.component_id,
            "evidence": f.evidence or [],
        }
        ev_files = {
            (ev.get("file") or "").replace("\\", "/").lstrip("./").lstrip("/")
            for ev in (f.evidence or [])
            if isinstance(ev, dict) and ev.get("file")
        }
        if (f.component_id and f.component_id in affected_comp_ids) or bool(ev_files & affected_files):
            item["scope"] = "change_specific"
            change_findings.append(item)
        else:
            item["scope"] = "repository_wide"
            repository_findings.append(item)

    # 5. Calculate Change Risk (using ONLY change-specific findings)
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
        findings=change_findings,
        unmapped_files=unmapped_files,
        total_routes_in_repo=total_routes,
    )

    # 6. Prioritize Tests
    prioritized_tests = prioritize_tests(mod_graph, graph_diff)

    # 7. Controlled Validation (run if explicitly requested)
    validation_result: dict[str, Any] | None = None
    if run_validation:
        validation_result = run_controlled_validation(
            project=project,
            graph_diff=graph_diff,
            prioritized_tests=prioritized_tests["tests"],
        )

    # 8. Merge Recommendation (strictly separating change-specific vs repo-wide findings)
    recommendation = generate_merge_recommendation(
        risk=risk_assessment,
        graph_diff=graph_diff,
        prioritized_tests=prioritized_tests,
        findings=change_findings,
        validation=validation_result,
        repository_findings=repository_findings,
        unmapped_files=unmapped_files,
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

    commit_sha = (project.ingest_stats or {}).get("commit_sha")
    if base_ref:
        resolved_base_ref = base_ref
    elif project.ast_branch and commit_sha:
        resolved_base_ref = f"{project.ast_branch}@{commit_sha[:7]}"
    else:
        resolved_base_ref = project.ast_branch or commit_sha
    resolved_head_ref = head_ref or ("working-tree" if (source_path / ".git").exists() else None)
    change_manifest["repository"] = {
        "project_id": project.id,
        "name": project.name,
        "source_type": project.source_type,
        "branch": project.ast_branch,
        "commit_sha": commit_sha,
    }
    change_manifest["unmapped_files"] = unmapped_files

    change_record = ChangeAnalysis(
        project_id=project.id,
        title=run_title,
        base_ref=resolved_base_ref,
        head_ref=resolved_head_ref,
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
