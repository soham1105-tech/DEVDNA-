"""Verified remediation: check a proposed patch against a disposable copy of
the project before anyone applies it to real files.

A remediation proposal is a set of full replacement file contents. The
verification is deliberately modest and honestly labelled:

1. copy the project source to a temp directory (never touching the original);
2. apply the proposed file contents to the copy;
3. re-run the full analysis pipeline on the patched copy;
4. compare findings by fingerprint (same definition the finding workflow uses).

A finding counts as `resolved_static` when its fingerprint no longer appears
in the patched analysis, `still_present` when it does. Findings that appear
only in the patched analysis are reported as introduced. Nothing is persisted
and no tests are executed: this is verification at the static-analysis level,
not proof the remediation is correct at runtime.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.analyzer.dispatch import analyse_repository
from app.graph.engine import build_graph
from app.services.finding_workflow import finding_fingerprint
from app.services.findings import generate_findings

MAX_FILES = 20
MAX_FILE_BYTES = 200_000


def _safe_rel(p: str) -> Path:
    """Normalise a proposed path; reject absolute paths and traversal."""
    if not p or "\x00" in p:
        raise ValueError("Empty or invalid file path")
    rel = Path(p.replace("\\", "/"))
    # note: on Windows, "/abs" is not is_absolute() but is drive-root-anchored
    # when joined (Path("C:/a") / "/abs" -> "C:/abs"), so anchor parts are rejected too
    if rel.is_absolute() or rel.root or rel.drive or ".." in rel.parts:
        raise ValueError(f"Path must be relative and stay inside the project: '{p}'")
    return rel


def _fingerprints(findings: list[dict]) -> dict[str, dict]:
    out = {}
    for f in findings:
        out[finding_fingerprint(f)] = f
    return out


def verify_remediation(source: Path, files: dict[str, str],
                       original_findings: list[dict],
                       check_ids: list[str] | None = None) -> dict:
    """Verify a proposed patch on a disposable copy.

    `original_findings` must be the FULL finding set of the run (it anchors
    the introduced-findings comparison); `check_ids` optionally restricts
    which findings get a per-finding status. Raises ValueError on invalid
    input (bad paths, too many/too large files)."""
    if not files:
        raise ValueError("No proposed file contents supplied")
    if len(files) > MAX_FILES:
        raise ValueError(f"Too many files ({len(files)}); maximum is {MAX_FILES}")
    rels = {}
    for p, content in files.items():
        if not isinstance(content, str):
            raise ValueError(f"File content for '{p}' must be a string")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"File '{p}' exceeds the {MAX_FILE_BYTES}-byte limit")
        rels[_safe_rel(p)] = content

    workdir = Path(tempfile.mkdtemp(prefix="devdna-remediation-"))
    try:
        patched = workdir / "patched"
        shutil.copytree(source, patched,
                        ignore=shutil.ignore_patterns(".git", ".venv", "venv", "node_modules",
                                                      "__pycache__", ".pytest_cache", "*.db",
                                                      "*.sqlite", "*.sqlite3", ".env", ".env.*"))
        for rel, content in rels.items():
            target = patched / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        result = analyse_repository(patched, use_cache=False)
        g = build_graph(result["components"], result["edges"])
        patched_findings = generate_findings(g, result)

        orig = _fingerprints(original_findings)
        patched = _fingerprints(patched_findings)
        results = []
        for f in original_findings:
            if check_ids is not None and f.get("id") not in set(check_ids):
                continue
            fp = finding_fingerprint(f)
            results.append({
                "finding_id": f.get("id"),
                "rule": f.get("rule"),
                "title": f.get("title"),
                "status": "still_present" if fp in patched else "resolved_static",
            })
        introduced = [pf for fp, pf in patched.items() if fp not in orig]
        results.sort(key=lambda r: (r["status"] != "still_present", r["finding_id"] or ""))
        introduced.sort(key=lambda f: (f.get("rule", ""), f.get("title", "")))
        return {
            "patched_files": sorted(str(r) for r in rels),
            "findings": results,
            "summary": {
                "checked": len(results),
                "resolved_static": sum(1 for r in results if r["status"] == "resolved_static"),
                "still_present": sum(1 for r in results if r["status"] == "still_present"),
                "introduced": len(introduced),
                "patched_components": len(result["components"]),
                "patched_edges": len(result["edges"]),
            },
            "introduced_findings": [
                {"rule": f.get("rule"), "title": f.get("title"),
                 "severity": f.get("severity"), "component_id": f.get("component_id")}
                for f in introduced[:50]
            ],
            "method": (
                "The proposed file contents were written to a disposable copy of the project, the "
                "full analysis pipeline was re-run on that copy, and findings were compared by "
                "fingerprint (the same definition used for suppression tracking). The original "
                "project was not modified."
            ),
            "limitations": [
                "'resolved_static' means the finding's fingerprint is absent from the patched "
                "analysis — it is not proof the remediation is correct or complete at runtime.",
                "No tests were executed; combine this with a controlled experiment for behavioural evidence.",
                "Findings are matched by fingerprint; a fix that merely renames or relocates the "
                "pattern may read as resolved plus introduced.",
            ],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
