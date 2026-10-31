"""Markdown and JSON reports. All content comes from persisted records and is redacted again on output."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.redact import redact
from app.graph.engine import __doc__ as SCORE_DOC
from app.models import AIInteraction, Experiment, Finding, Prediction, Project
from app.services.analysis import component_dicts
from app.services.finding_workflow import finding_fingerprint, suppression_map
from app.services.serialize import STATUS_LABELS, experiment_out, finding_out

LIMITATIONS = [
    "Architecture is discovered by static analysis of Python source; dynamic behaviour is not observed.",
    "Criticality scores are structural heuristics, not production failure probabilities.",
    "Experiment results apply to the project's own test suite under one injected fault in an isolated harness.",
]


def _scrub(obj):
    """Redact every string in a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj)[0]
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    return obj


def next_test_suggestion(exp: dict) -> str:
    rows = exp.get("comparison", {}).get("rows", [])
    mism = [r for r in rows if r.get("verdict") == "mismatch"]
    if mism:
        r = mism[0]
        return (f"Investigate {r['nodeid']}: predicted '{r['prediction']}' but observed '{r['observed']}'. Add a focused test "
                f"that asserts the behaviour along {' -> '.join(r.get('path') or []) or 'its call path'}.")
    unc = [r for r in rows if r.get("verdict", "").startswith("uncertain")]
    if unc:
        return f"Add an assertion that pins down the handled-error behaviour of {unc[0]['nodeid']} (currently uncertain)."
    return "Run the same scenario against another dependency, or add a test for an untested route."


def project_report(db: Session, project: Project) -> dict:
    run_id = project.latest_run_id
    comps = component_dicts(db, run_id) if run_id else []
    from app.models import AnalysisRun

    run = db.get(AnalysisRun, run_id) if run_id else None
    findings = db.scalars(select(Finding).where(Finding.run_id == run_id)).all() if run_id else []
    suppressions = suppression_map(db, findings)
    preds = db.scalars(select(Prediction).where(Prediction.project_id == project.id)).all()
    exps = db.scalars(select(Experiment).where(Experiment.project_id == project.id)).all()
    ai = db.scalars(select(AIInteraction).where(AIInteraction.project_id == project.id)).all()
    top = sorted([c for c in comps if c["type"] != "test"], key=lambda c: -c["metrics"].get("criticality", 0))[:10]
    report = {
        "report_type": "devdna_project_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {"id": project.id, "name": project.name, "source_type": project.source_type},
        "analysis": (run.summary if run else {}),
        "top_critical_components": [{"id": c["id"], "type": c["type"], **{k: c["metrics"].get(k) for k in
                                     ("criticality", "dependents", "betweenness", "routes_depending")}} for c in top],
        "criticality_formula": SCORE_DOC.strip(),
        "findings": [finding_out(f, suppressions.get(finding_fingerprint(f))) for f in findings],
        "predictions": [{"id": p.id, "scenario": p.scenario, "target": p.target_id, "summary": p.impact.get("summary"),
                         "ai_explained": bool(p.ai_explanation)} for p in preds],
        "experiments": [{"id": e.id, "status": e.status, "status_label": STATUS_LABELS.get(e.status, e.status),
                         "mode": e.mode, "scenario": e.prediction.scenario if e.prediction else None,
                         "metrics": (e.comparison or {}).get("metrics")} for e in exps],
        "ai_usage": {"requests": len(ai), "ok": sum(1 for a in ai if a.status == "ok"),
                     "models": sorted({a.model for a in ai})},
        "limitations": LIMITATIONS,
    }
    return _scrub(report)


def project_markdown(r: dict) -> str:
    a = r["analysis"] or {}
    cov = a.get("coverage", {})
    lines = [f"# DevDNA report: {r['project']['name']}", "", f"_Generated {r['generated_at']}_", "",
             "## Analysis summary", ""]
    lines += [f"- Files scanned: {a.get('scan', {}).get('file_count', 'n/a')}",
              f"- Languages: {', '.join(f'{k} ({v})' for k, v in a.get('scan', {}).get('languages', {}).items()) or 'n/a'}",
              f"- Frameworks detected: {', '.join(a.get('frameworks', [])) or 'none'}",
              f"- Components: {a.get('component_counts', {})}",
              f"- Relationships: {a.get('edge_counts', {})}",
              f"- Edge evidence: {a.get('edge_classification', {})}",
              f"- Call resolution rate: {cov.get('call_resolution_rate')} ({cov.get('unresolved_calls', 0)} unresolved calls)",
              f"- Routes with tests: {cov.get('routes_with_tests')} / {cov.get('routes')}", "",
              "## Most critical components", "", "| Component | Type | Criticality | Dependents | Routes |", "|---|---|---|---|---|"]
    for c in r["top_critical_components"]:
        lines.append(f"| `{c['id']}` | {c['type']} | {c['criticality']} | {c['dependents']} | {c['routes_depending']} |")
    lines += ["", "<details><summary>Criticality formula</summary>", "", "```", r["criticality_formula"], "```", "</details>",
              "", "## Findings", ""]
    for f in r["findings"]:
        lines.append(f"### [{f['severity'].upper()}] {f['title']}")
        lines.append(f"{f['description']}  ")
        lines.append(f"Confidence: {f['confidence']} · Review: {f['review_status']}  ")
        for ev in f["evidence"][:4]:
            if ev.get("file"):
                lines.append(f"- `{ev.get('file')}:{ev.get('line')}` {('`' + ev['snippet'] + '`') if ev.get('snippet') else ''}")
        lines.append(f"\n**Mitigation:** {f['mitigation']}\n")
    lines += ["## Experiments", ""]
    if not r["experiments"]:
        lines.append("No experiments executed yet.")
    for e in r["experiments"]:
        m = e.get("metrics") or {}
        lines.append(f"- `{e['id']}` {e['scenario']}: **{e['status_label']}** (mode: {e['mode']}, accuracy: {m.get('accuracy')})")
    lines += ["", "## AI usage", "", f"{r['ai_usage']['requests']} Gemma requests ({r['ai_usage']['ok']} successful), "
              f"models: {', '.join(r['ai_usage']['models']) or 'none'}", "", "## Limitations", ""]
    lines += [f"- {x}" for x in r["limitations"]]
    return "\n".join(lines) + "\n"


def experiment_report(exp: Experiment) -> dict:
    e = experiment_out(exp)
    pred = e.get("prediction") or {}
    impact = pred.get("impact", {})
    obs = e["observation"]
    report = {
        "report_type": "devdna_experiment_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": e["id"],
        "status": e["status"], "status_label": e["status_label"], "mode": e["mode"],
        "scenario": impact.get("scenario_label"), "target": impact.get("target", {}).get("id"),
        "fault": impact.get("fault_description"),
        "fault_applied": obs.get("faulted", {}).get("fault_state", {}).get("applied", []),
        "prediction": {"summary": impact.get("summary"), "method": impact.get("method"),
                       "affected": [{"id": a["id"], "certainty": a["certainty"], "distance": a["distance"]}
                                    for a in impact.get("affected", [])]},
        "ai_prediction_explanation": pred.get("ai_explanation"),
        "observation": {k: {kk: v.get(kk) for kk in ("counts", "duration_s", "exit_code", "timed_out")}
                        for k, v in obs.items() if k in ("baseline", "faulted")},
        "comparison": e["comparison"],
        "ai_interpretation": e["ai_interpretation"],
        "logs": {"faulted_output": obs.get("faulted", {}).get("output", "")[-6000:]},
        "recommended_next_test": (e.get("ai_interpretation") or {}).get("result", {}).get("next_test") or next_test_suggestion(e),
        "limitations": LIMITATIONS + impact.get("limitations", []),
        "blocked_reason": e["error"] if e["status"] == "blocked" else None,
    }
    return _scrub(report)


def experiment_markdown(r: dict) -> str:
    c = r.get("comparison") or {}
    m = c.get("metrics") or {}
    lines = [f"# Experiment report `{r['experiment_id']}`", "", f"_Generated {r['generated_at']}_", "",
             f"**Status:** {r['status_label']}  ", f"**Mode:** {r['mode']}  ",
             f"**Scenario:** {r['scenario']} on `{r['target']}`  ", f"**Fault:** {r['fault']}", ""]
    if r.get("blocked_reason"):
        lines += ["## Blocked", "", r["blocked_reason"], ""]
    lines += ["## Fault actually applied", ""] + [f"- {x}" for x in r["fault_applied"]] + [""]
    lines += ["## Prediction vs observation", "", c.get("reason", ""), "",
              f"Accuracy {m.get('accuracy')} · precision {m.get('precision')} · recall {m.get('recall')} · "
              f"fault triggered {m.get('fault_triggers')} times", "",
              "| Test | Predicted | Baseline | Observed | Verdict |", "|---|---|---|---|---|"]
    for row in c.get("rows", []):
        lines.append(f"| `{row['nodeid']}` | {row['prediction']} | {row['baseline']} | {row['observed']} | {row['verdict']} |")
    lines += [""] + [f"> {n}" for n in c.get("notes", [])] + [""]
    ai = (r.get("ai_interpretation") or {}).get("result")
    if ai:
        lines += [f"## Gemma interpretation ({r['ai_interpretation'].get('meta', {}).get('model')})", "", ai.get("verdict_explanation", ""), ""]
        for d in ai.get("discrepancies", []):
            lines.append(f"- **{d['test']}**: {d['explanation']}")
        lines.append("")
    lines += ["## Recommended next test", "", r["recommended_next_test"], "", "## Test log (faulted run, redacted)", "",
              "```", r["logs"]["faulted_output"][-4000:], "```", "", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]]
    return "\n".join(lines) + "\n"


def to_json(r: dict) -> str:
    return json.dumps(r, indent=2, default=str)
