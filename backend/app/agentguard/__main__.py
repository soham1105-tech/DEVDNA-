"""CLI: run the AgentGuard audit on a repository and print a report.

Usage: python -m app.agentguard <repo-path> [--json <out.json>] [--markdown <out.md>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agentguard.service import audit_repository


def _markdown(result: dict) -> str:
    lines = [f"# AgentGuard audit: {result['repo']}", ""]
    lines.append(f"Scanned {result['scanned']['files']} config file(s), {result['scanned']['records']} record(s).")
    lines.append(f"Findings: {len(result['findings'])} "
                 f"({result['status_counts']['verified']} verified, {result['status_counts']['suspected']} suspected).")
    lines.append("")
    if not result["findings"]:
        lines.append("No findings. This does not mean the agent configuration is secure — "
                     "see limitations in the API output.")
    for f in result["findings"]:
        lines.append(f"## [{f['severity'].upper()}] {f['title']}")
        lines.append(f"- Category: {f['category']} | Status: **{f['status']}** | Confidence: {f['confidence']}")
        for e in f["evidence"]:
            lines.append(f"  - Evidence: {e.get('file', '')}:{e.get('line', 0)} — {e.get('snippet', '')} ({e.get('detail', '')})")
        if f.get("required_permission"):
            lines.append(f"  - Required permission: {f['required_permission']}")
        if f.get("missing_safeguard"):
            lines.append(f"  - Missing safeguard: {f['missing_safeguard']}")
        lines.append(f"  - Recommendation: {f['recommendation']}")
        lines.append("")
    if result["unsupported"]:
        lines.append("## Unsupported files")
        for u in result["unsupported"]:
            lines.append(f"- {u['file']}: {u['reason']}")
        lines.append("")
    lines.append("## Limitations")
    for lim in result["limitations"]:
        lines.append(f"- {lim}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentguard",
                                     description="Deterministic security audit of AI-agent configurations.")
    parser.add_argument("path", help="Repository path to audit")
    parser.add_argument("--json", dest="json_out", help="Write the full JSON result to this file")
    parser.add_argument("--markdown", dest="md_out", help="Write a Markdown report to this file")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.is_dir():
        print(f"agentguard: not a directory: {root}", file=sys.stderr)
        return 2
    result = audit_repository(root)
    result["repo"] = str(root)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"JSON report written: {args.json_out}")
    md = _markdown(result)
    if args.md_out:
        Path(args.md_out).write_text(md, encoding="utf-8")
        print(f"Markdown report written: {args.md_out}")
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
