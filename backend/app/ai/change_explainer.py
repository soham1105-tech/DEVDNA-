"""DevDNA Change Intelligence - Gemini Grounded Reasoning Layer.

Translates deterministic change intelligence facts into clear engineering explanations:
- What changed in the code
- How the failure propagates to downstream services and routes
- Why the change is risky
- Explanation of empirical experiment results
- Suggested architectural repairs / fault boundaries
- Next actions for the developer

Enforces:
- Evidence grounding: strictly based on deterministic inputs
- No hallucinations: does not invent components, tests, or success states
- Graceful deterministic fallback when Gemini API key is absent or unavailable.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.ai.gemma import AIError, extract_json
from app.config import settings

log = logging.getLogger("devdna.change_explainer")


class ChangeExplanation(BaseModel):
    summary: str
    propagation_narrative: str
    failure_risks: list[str] = Field(default_factory=list)
    validation_explanation: str
    suggested_repairs: list[str] = Field(default_factory=list)
    developer_next_steps: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)


def build_deterministic_fallback(
    change_manifest: dict[str, Any],
    graph_diff: dict[str, Any],
    risk: dict[str, Any],
    prioritized_tests: dict[str, Any],
    validation: dict[str, Any] | None,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    """Generate an honest, deterministic explanation when Gemini is not available."""
    files_summary = change_manifest.get("summary", {})
    diff_summary = graph_diff.get("summary", {})
    directly = graph_diff.get("directly_affected", [])
    routes = graph_diff.get("impacted_routes", [])
    critical = graph_diff.get("critical_components_affected", [])

    direct_names = [c["name"] for c in directly[:3]]
    direct_str = ", ".join(direct_names) if direct_names else "analysed files"

    summary = (
        f"This code change modifies {files_summary.get('total_files', 0)} file(s) touching "
        f"{diff_summary.get('directly_affected_count', 0)} direct component(s) ({direct_str}). "
        f"Overall risk assessment: {risk.get('score', 0)}/100 ({risk.get('level', 'low').upper()}). "
        f"Merge recommendation: {recommendation.get('decision', 'REVIEW REQUIRED')}."
    )

    if routes:
        route_str = ", ".join([r["route"] for r in routes[:3]])
        propagation_narrative = (
            f"The change propagates downstream to {diff_summary.get('transitively_affected_count', 0)} component(s) "
            f"and reaches {len(routes)} client-facing API route(s): {route_str}. "
            f"{'Core critical hub(s) like ' + critical[0]['name'] + ' are in the reachability path.' if critical else ''}"
        )
    else:
        propagation_narrative = (
            f"The change propagates to {diff_summary.get('transitively_affected_count', 0)} downstream component(s). "
            f"No external API routes are directly in the reachability path."
        )

    failure_risks = [f["reason"] for f in risk.get("factors", []) if f.get("raw_score", 0) >= 50]
    if not failure_risks:
        failure_risks = ["Low direct propagation risk detected within static analysis boundaries."]

    if validation and validation.get("executed"):
        faulted_failed = validation.get("faulted_summary", {}).get("failed", 0)
        validation_explanation = (
            f"Controlled empirical validation was executed under '{validation.get('fault', {}).get('kind', 'fault')}' fault conditions. "
            f"Baseline tests passed; with the injected fault, {faulted_failed} test(s) failed as predicted "
            f"(accuracy: {validation.get('accuracy', 'N/A')})."
        )
    elif validation and validation.get("status") == "blocked":
        validation_explanation = (
            f"Controlled validation was blocked: {validation.get('reason', 'Sandbox unavailable')}. "
            f"Predictions remain static reachability assessments without empirical verification."
        )
    else:
        validation_explanation = (
            "Empirical validation experiment has not been executed for this change run. "
            "Predictions reflect static dependency reachability."
        )

    suggested_repairs = [
        "Wrap critical downstream service calls in graceful fallback handlers (try/except or default values).",
        "Add explicit health or circuit-breaker checks if modifying database or network connection factories.",
        "Ensure all modified functions have corresponding Tier 1 unit tests exercising both happy and failure paths.",
    ]

    developer_next_steps = recommendation.get("recommended_actions") or [
        "Review affected downstream routes and run prioritized Tier 1 and Tier 2 tests before merging.",
    ]

    return {
        "summary": summary,
        "propagation_narrative": propagation_narrative,
        "failure_risks": failure_risks,
        "validation_explanation": validation_explanation,
        "suggested_repairs": suggested_repairs,
        "developer_next_steps": developer_next_steps,
        "source": "deterministic_core",
        "evidence_used": ["E:graph_diff", "E:risk_breakdown", "E:prioritized_tests"],
    }


def explain_change(
    change_manifest: dict[str, Any],
    graph_diff: dict[str, Any],
    risk: dict[str, Any],
    prioritized_tests: dict[str, Any],
    validation: dict[str, Any] | None,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    """Generate an evidence-grounded explanation using Gemini if configured, or deterministic fallback."""
    if not settings.api_key:
        return build_deterministic_fallback(
            change_manifest, graph_diff, risk, prioritized_tests, validation, recommendation
        )

    prompt = f"""
Analyze this code change using ONLY the deterministic evidence provided below.

E1 (Change Summary):
Files: {change_manifest.get('summary', {})}
Direct Components: {[c['name'] for c in graph_diff.get('directly_affected', [])[:5]]}

E2 (Propagation & Reachability):
Directly Affected: {graph_diff.get('summary', {}).get('directly_affected_count', 0)}
Transitively Affected: {graph_diff.get('summary', {}).get('transitively_affected_count', 0)}
Impacted API Routes: {[r['route'] for r in graph_diff.get('impacted_routes', [])[:5]]}
Critical Components: {[c['name'] for c in graph_diff.get('critical_components_affected', [])[:3]]}

E3 (Risk Assessment):
Score: {risk.get('score', 0)}/100 ({risk.get('level', 'low')})
Factors: {[f['name'] + ': ' + f['reason'] for f in risk.get('factors', []) if f.get('raw_score', 0) >= 40]}

E4 (Prioritized Tests):
Total: {prioritized_tests.get('summary', {}).get('total_prioritized_tests', 0)}
Tier 1 Direct: {prioritized_tests.get('summary', {}).get('tier_1_direct', 0)}
Tier 4 Integration: {prioritized_tests.get('summary', {}).get('tier_4_integration', 0)}

E5 (Merge Recommendation):
Decision: {recommendation.get('decision')}
Severity: {recommendation.get('severity')}
Blockers: {recommendation.get('blockers')}

Return JSON with:
- summary: 2-3 sentences explaining what changed in plain English.
- propagation_narrative: How the change flows downstream to services and user-facing routes.
- failure_risks: List of concrete failure modes that could occur.
- validation_explanation: Status of empirical validation and what it proved or could not prove.
- suggested_repairs: 2-4 concrete code/architectural remediations to prevent breakage.
- developer_next_steps: Concrete checklist of next actions for the author.
- evidence_used: List of cited evidence IDs (e.g. ["E1", "E2", "E3"]).
"""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.api_key)
        response = client.models.generate_content(
            model=settings.gemma_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        parsed = extract_json(response.text)
        validated = ChangeExplanation(**parsed)
        data = validated.model_dump()
        data["source"] = "gemini"
        return data
    except Exception as e:
        log.warning("Gemini change explanation failed, using deterministic fallback: %s", e)
        fallback = build_deterministic_fallback(
            change_manifest, graph_diff, risk, prioritized_tests, validation, recommendation
        )
        fallback["gemini_error"] = str(e)
        return fallback
