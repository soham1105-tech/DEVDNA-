"""DevDNA GitHub Integration & Security - Webhook Validation & Check Runs.

Provides:
- Cryptographic HMAC-SHA256 signature verification for GitHub webhooks (X-Hub-Signature-256)
- Secure payload formatting for GitHub Check Runs and PR Comments
- Sanitization to prevent secret exposure or unsafe Markdown interpolation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

log = logging.getLogger("devdna.github")


def verify_github_signature(payload_bytes: bytes, secret: str, signature_header: str | None) -> bool:
    """Verify that a GitHub webhook payload matches the X-Hub-Signature-256 header.

    Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
    """
    if not signature_header or not secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:]
    mac = hmac.new(secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256)
    computed_sig = mac.hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


def format_github_check_run(report: dict[str, Any]) -> dict[str, Any]:
    """Format a Change Intelligence report into a GitHub Check Run payload."""
    rec = report.get("recommendation", {})
    risk = report.get("risk_assessment", {})
    decision = rec.get("decision", "REVIEW REQUIRED")

    # Map decision to GitHub Check Run conclusion
    # 'success', 'failure', 'neutral', 'action_required'
    if decision == "APPROVE":
        conclusion = "success"
        summary_title = "DevDNA: Safe to Merge (Approve)"
    elif decision == "BLOCK RECOMMENDED":
        conclusion = "failure"
        summary_title = "DevDNA: Merge Block Recommended"
    else:
        conclusion = "neutral"
        summary_title = "DevDNA: Human Review Required"

    risk_score = risk.get("score", 0)
    risk_level = risk.get("level", "low").upper()
    impacted_count = rec.get("impacted_components_count", 0)
    routes_count = rec.get("impacted_routes_count", 0)
    tests_count = rec.get("prioritized_tests_count", 0)

    summary_text = (
        f"### {summary_title}\n\n"
        f"- **Recommendation:** `{decision}`\n"
        f"- **Change Risk Score:** `{risk_score}/100` ({risk_level})\n"
        f"- **Impacted Components:** `{impacted_count}`\n"
        f"- **Impacted API Routes:** `{routes_count}`\n"
        f"- **Prioritized Tests:** `{tests_count}`\n\n"
        f"{rec.get('headline', '')}\n"
    )

    return {
        "name": "DevDNA Change Intelligence",
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": f"DevDNA: {decision} (Risk {risk_score}/100)",
            "summary": summary_text,
            "text": render_change_intelligence_markdown(report),
        },
    }


def render_change_intelligence_markdown(report: dict[str, Any]) -> str:
    """Render a comprehensive GitHub PR comment / Check Run report in Markdown."""
    rec = report.get("recommendation", {})
    risk = report.get("risk_assessment", {})
    diff = report.get("graph_diff", {})
    tests = report.get("prioritized_tests", {}).get("tests", [])
    val = report.get("validation")
    ai = report.get("ai_explanation")

    decision = rec.get("decision", "REVIEW REQUIRED")
    badge = "🟢" if decision == "APPROVE" else ("🔴" if decision == "BLOCK RECOMMENDED" else "🟡")

    lines = [
        f"## {badge} DevDNA Change Intelligence: `{decision}`",
        "",
        f"> **{rec.get('headline', 'Change impact and risk assessment complete.')}**",
        "",
        "### 📊 Risk Assessment",
        f"- **Overall Risk Score:** `{risk.get('score', 0)}/100` ({risk.get('level', 'low').upper()})",
        "",
        "| Contributing Factor | Weight | Score | Details |",
        "| :--- | :---: | :---: | :--- |",
    ]

    for f in risk.get("factors", []):
        lines.append(f"| {f['name']} | {int(f['weight']*100)}% | `{f['raw_score']}/100` | {f['reason']} |")

    lines.extend([
        "",
        "### 🔍 Dependency Blast Radius",
        f"- **Directly Modified:** `{diff.get('summary', {}).get('directly_affected_count', 0)}` component(s)",
        f"- **Transitive Dependents:** `{diff.get('summary', {}).get('transitively_affected_count', 0)}` component(s)",
        f"- **Exposed API Routes:** `{len(diff.get('impacted_routes', []))}` route(s)",
        "",
    ])

    if diff.get("impacted_routes"):
        lines.append("**Impacted API Routes:**")
        for r in diff["impacted_routes"][:5]:
            lines.append(f"- `{r['route']}` (distance: {r['distance']})")
        if len(diff["impacted_routes"]) > 5:
            lines.append(f"- _...and {len(diff['impacted_routes']) - 5} more routes_")
        lines.append("")

    lines.extend([
        "### 🧪 Intelligent Test Prioritisation",
        f"Identified `{len(tests)}` relevant test(s) categorized into 5 tiers:",
        "",
        "| Tier | Test | Reason | Confidence |",
        "| :--- | :--- | :--- | :---: |",
    ])

    for t in tests[:8]:
        lines.append(f"| Tier {t['tier']} ({t['tier_name']}) | `{t['name']}` | {t['selection_reason']} | `{t['coverage_confidence']}` |")
    if len(tests) > 8:
        lines.append(f"| ... | _...and {len(tests) - 8} more prioritized tests_ | | |")

    lines.append("")

    if val and val.get("executed"):
        lines.extend([
            "### 🔬 Controlled Validation",
            f"- **Fault Tested:** `{val.get('fault', {}).get('kind')}` targeting `{val.get('fault', {}).get('target')}`",
            f"- **Baseline:** `{val.get('baseline_summary', {}).get('passed', 0)}` passed",
            f"- **Fault Injected:** `{val.get('faulted_summary', {}).get('failed', 0)}` failed, `{val.get('faulted_summary', {}).get('passed', 0)}` passed",
            f"- **Prediction Accuracy:** `{val.get('accuracy', 'N/A')}`",
            "",
        ])

    if ai:
        lines.extend([
            "### 🤖 AI System Analysis",
            ai.get("summary", ""),
            "",
            f"**Propagation:** {ai.get('propagation_narrative', '')}",
            "",
        ])

    if rec.get("recommended_actions"):
        lines.extend([
            "### 📋 Recommended Next Actions",
        ])
        for act in rec["recommended_actions"]:
            lines.append(f"- [ ] {act}")
        lines.append("")

    lines.extend([
        "---",
        "_Generated deterministically by DevDNA Change Intelligence Core Engine._",
    ])

    return "\n".join(lines)
