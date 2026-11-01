"""Test-gap validation: which analysed code has no test reaching it, and how
trustworthy that coverage map is.

Two layers, kept honestly separate:

* **Static layer** — a multi-source forward BFS from every test node over
  propagating edges gives the set of components some test can reach. Every
  other component is a *static* gap. This is a claim about the graph, not
  about runtime: dynamic dispatch and parametrisation are not modelled.
* **Validation layer** — completed experiments recorded which tests actually
  executed (junit results). Static coverage claims whose covering tests were
  observed executing are marked `validated`; everything else stays
  `static_only`. A gap (absence of coverage) can never be validated by this
  data — absence of evidence is labelled as such.
"""
from __future__ import annotations

from collections import deque

from app.graph.engine import PROPAGATING
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Experiment

# component types that are infrastructure nodes rather than code a test could
# meaningfully "reach"; they are excluded from gap counts (reported separately)
NON_CODE_TYPES = {"database", "external_api", "env_var", "worker", "route_group"}


def _exercised(g) -> tuple[dict[str, int], int]:
    """Forward multi-source BFS from all test nodes. Returns (distance-by-node
    for exercised code, test_count). Tests reaching tests is not propagation."""
    dist: dict[str, int] = {}
    q: deque[str] = deque()
    test_count = 0
    for n, data in g.nodes(data=True):
        if data["type"] == "test":
            test_count += 1
            dist[n] = 0
            q.append(n)
    while q:
        cur = q.popleft()
        for _, dst, data in g.out_edges(cur, data=True):
            if data["type"] not in PROPAGATING or dst in dist:
                continue
            dist[dst] = dist[cur] + 1
            q.append(dst)
    return dist, test_count


def compute_test_gaps(g) -> dict:
    """Static test-gap map per file. Deterministic: sorted outputs, ties by id."""
    dist, test_count = _exercised(g)

    files: dict[str, dict] = {}
    for n, data in g.nodes(data=True):
        ntype = data["type"]
        f = data.get("file")
        if not f or ntype == "test":
            continue
        entry = files.setdefault(f, {"file": f, "components": 0, "covered": 0,
                                     "uncovered": 0, "gap_ids": [], "min_distance": None})
        if ntype in NON_CODE_TYPES:
            continue
        entry["components"] += 1
        if n in dist:
            entry["covered"] += 1
            d = dist[n]
            entry["min_distance"] = d if entry["min_distance"] is None else min(entry["min_distance"], d)
        else:
            entry["uncovered"] += 1
            entry["gap_ids"].append(n)
    for entry in files.values():
        entry["gap_ids"].sort()
    file_gaps = sorted((e for e in files.values() if e["uncovered"] > 0),
                       key=lambda e: (-e["uncovered"], e["file"]))
    fully_covered = sorted(e["file"] for e in files.values() if e["uncovered"] == 0)

    # per-type gap roll-up so untestable shapes are visible without guessing
    by_type: dict[str, int] = {}
    for n, data in g.nodes(data=True):
        if data["type"] in ("test",) or data.get("file") is None:
            continue
        if n not in dist and data["type"] not in NON_CODE_TYPES:
            by_type[data["type"]] = by_type.get(data["type"], 0) + 1

    total_code = sum(e["components"] for e in files.values())
    return {
        "test_count": test_count,
        "files_analysed": len(files),
        "file_gaps": file_gaps,
        "fully_covered_files": fully_covered,
        "uncovered_by_type": dict(sorted(by_type.items())),
        "summary": {
            "code_components": total_code,
            "covered_components": sum(e["covered"] for e in files.values()),
            "uncovered_components": sum(e["uncovered"] for e in files.values()),
            "files_with_gaps": len(file_gaps),
            "files_fully_covered": len(fully_covered),
        },
        "method": (
            "Multi-source forward breadth-first search over propagating edges "
            "(calls, handled_by, served_by, runs_on_startup, uses_database, calls_http, reads_env, "
            "exercises) from every test node; components no test can reach are static gaps."
        ),
        "limitations": [
            "Static reachability from a test is not proof the test asserts meaningful behaviour "
            "of that component; it only shows the component is on some executed path.",
            "Dynamic dispatch, reflection and monkeypatched targets are not modelled, so gaps "
            "can be false positives and coverage can be false negatives.",
            "Absence of coverage is never 'validated' — only positive coverage claims can be "
            "confirmed by experiment observations.",
        ],
    }


def _executed_nodeids(db: Session, project_id: str) -> tuple[set[str], int]:
    """junit keys / nodeids of tests that actually executed in completed
    experiments for this project (baseline runs)."""
    executed: set[str] = set()
    experiment_count = 0
    rows = db.scalars(select(Experiment).where(Experiment.project_id == project_id)).all()
    for exp in rows:
        obs = exp.observation or {}
        baseline = obs.get("baseline") or {}
        results = baseline.get("results") or {}
        if results:
            experiment_count += 1
        for key in results:
            executed.add(str(key))
    return executed, experiment_count


def validate_gaps(g, gaps: dict, executed: set[str], experiment_count: int) -> dict:
    """Attach validation info to the static gap map. Coverage claims are
    `validated` only when at least one covering test of that file was observed
    executing in a completed experiment."""
    validated_files: dict[str, int] = {}
    for n, data in g.nodes(data=True):
        if data["type"] != "test":
            continue
        td = data.get("data", {})
        keys = {td.get("nodeid"), td.get("junit_key"), data.get("name")}
        if not (keys & executed):
            continue
        # this test actually executed: everything it reaches is validated
        seen: deque[str] = deque([n])
        visited = {n}
        while seen:
            cur = seen.popleft()
            for _, dst, edata in g.out_edges(cur, data=True):
                if edata["type"] not in PROPAGATING or dst in visited:
                    continue
                visited.add(dst)
                seen.append(dst)
                f = g.nodes[dst].get("file")
                if f:
                    validated_files[f] = validated_files.get(f, 0) + 1

    validation = {
        "experiments_observed": experiment_count,
        "tests_observed_executing": len(executed),
        "files_validated": len(validated_files),
        "status": "validated" if experiment_count else "static_only",
        "note": (
            "Coverage claims below are static-graph claims. "
            + (f"{len(validated_files)} file(s) had covering tests that were observed executing in "
               f"{experiment_count} completed experiment(s)."
               if experiment_count else
               "No completed experiment has executed this project's tests, so no coverage "
               "claim is validated yet. Run an experiment to validate the coverage map.")
        ),
    }
    validated_files_sorted = dict(sorted(validated_files.items()))
    return {**gaps, "validation": validation, "validated_files": validated_files_sorted}


def test_gaps(db: Session, g, project_id: str) -> dict:
    """Full report: static gaps + validation against experiment observations."""
    gaps = compute_test_gaps(g)
    executed, count = _executed_nodeids(db, project_id)
    return validate_gaps(g, gaps, executed, count)
