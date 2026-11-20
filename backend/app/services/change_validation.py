"""DevDNA Change Intelligence - Controlled Validation Service.

Executes baseline and fault-injected experiments on prioritized tests
to empirically validate predicted impact against reality.
Produces confusion matrix and prediction vs reality breakdown.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.experiments.runner import RunnerBlocked, choose_mode, compare, execute, fault_spec
from app.models import Project

log = logging.getLogger("devdna.change_validation")


def select_appropriate_fault(graph_diff: dict[str, Any]) -> dict[str, Any]:
    """Select the most representative controlled fault based on the changed components."""
    directly = graph_diff.get("directly_affected", [])
    all_names = " ".join([c.get("name", "") + " " + (c.get("file") or "") for c in directly]).lower()

    if any(k in all_names for k in ("db", "database", "session", "sql", "engine", "models")):
        return {"kind": "db_unavailable", "target": "database connection pool"}
    if any(k in all_names for k in ("http", "client", "request", "api", "fetch", "url")):
        return {"kind": "http_unavailable", "target": "outbound HTTP endpoints"}
    return {"kind": "db_unavailable", "target": "core resource handler"}


def run_controlled_validation(
    project: Project,
    graph_diff: dict[str, Any],
    prioritized_tests: list[dict[str, Any]],
    fault: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run baseline and faulted test execution and compare prediction vs observation."""
    mode, block_reason = choose_mode(project.trusted)
    if mode == "blocked":
        return {
            "status": "blocked",
            "executed": False,
            "reason": block_reason,
            "baseline": None,
            "faulted": None,
            "comparison": None,
            "limitations": [block_reason],
        }

    chosen_fault = fault or select_appropriate_fault(graph_diff)
    source_path = Path(project.source_path)

    try:
        obs = execute(source_path, chosen_fault, mode)
    except Exception as e:
        log.error("Experiment execution failed: %s", e, exc_info=True)
        return {
            "status": "failed",
            "executed": False,
            "error": str(e),
            "reason": f"Execution error during test harness execution: {e}",
            "limitations": ["Test execution failed unexpectedly."],
        }

    # Format predictions for compare()
    predicted_tests = []
    for t in prioritized_tests:
        # Tier 1, 2, 4 are predicted to fail when the fault is injected into their dependency
        # Unless mocked
        is_mocked = t.get("is_mocked", False)
        prediction = "pass" if is_mocked else ("fail" if t["tier"] in (1, 2, 4) else "may_break")
        predicted_tests.append({
            "name": t["name"],
            "nodeid": t.get("nodeid"),
            "junit_key": t.get("nodeid") or t["name"],
            "prediction": prediction,
            "path": t.get("related_components", []),
            "handled_at": None,
        })

    impact_mock = {
        "tests": predicted_tests,
        "summary": {"prediction": "controlled_experiment"},
    }

    comp = compare(impact_mock, obs)

    # Determine actually affected components
    actually_affected_ids = set()
    for row in comp.get("rows", []):
        if row.get("observed") in ("failed", "error"):
            actually_affected_ids.update(row.get("path", []))

    predicted_ids = {c["id"] for c in graph_diff.get("directly_affected", [])} | {c["id"] for c in graph_diff.get("transitively_affected", [])}

    return {
        "status": "completed",
        "executed": True,
        "mode": mode,
        "fault": chosen_fault,
        "baseline_summary": {
            "passed": obs["baseline"]["counts"]["passed"],
            "failed": obs["baseline"]["counts"]["failed"],
            "errors": obs["baseline"]["counts"]["error"],
            "duration": obs["baseline"]["duration_s"],
        },
        "faulted_summary": {
            "passed": obs["faulted"]["counts"]["passed"],
            "failed": obs["faulted"]["counts"]["failed"],
            "errors": obs["faulted"]["counts"]["error"],
            "duration": obs["faulted"]["duration_s"],
            "fault_triggered": obs["faulted"]["fault_state"].get("triggers", 0) > 0,
        },
        "metrics": comp.get("metrics", {}),
        "comparison_status": comp.get("status"),
        "comparison_rows": comp.get("rows", []),
        "predicted_affected_components": sorted(predicted_ids),
        "actually_affected_components": sorted(actually_affected_ids),
        "correct_predictions": comp.get("metrics", {}).get("true_positive", 0) + comp.get("metrics", {}).get("true_negative", 0),
        "false_positives": comp.get("metrics", {}).get("false_positive", 0),
        "false_negatives": comp.get("metrics", {}).get("false_negative", 0),
        "accuracy": comp.get("metrics", {}).get("accuracy"),
        "limitations": [
            "Validation reflects the injected fault behavior and tested scenarios; not an exhaustive production guarantee.",
        ],
    }
