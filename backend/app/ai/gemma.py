"""Gemma 4 integration via the official Google Gen AI SDK (google-genai).

* The model ID comes from GEMMA_MODEL (default gemma-4-26b-a4b-it). It is never
  silently swapped for another model: if the configured model fails, the
  request fails and the UI shows the error.
* The key is read server-side from GEMINI_API_KEY / GOOGLE_API_KEY and never logged.
* Responses must be JSON matching a Pydantic schema; invalid output triggers one
  repair attempt and is then reported as `invalid_output`.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from app.config import settings

SYSTEM_INSTRUCTION = (
    "You are DevDNA's reliability analyst. You explain software architecture and failure impact using ONLY the "
    "evidence provided. Each piece of evidence has an id like E3. Cite evidence ids for every claim. If the evidence "
    "does not support a conclusion, say so explicitly and list it as an uncertainty. Never invent files, functions, "
    "services or test results. The deterministic graph analysis is the source of truth for WHICH components are "
    "affected; your job is to explain WHY, state assumptions, and suggest tests and mitigations. "
    "Reply with a single JSON object that matches the requested schema. No prose outside the JSON."
)


# ----------------------------------------------------------------------------- response schemas
class Layer(BaseModel):
    name: str
    role: str
    components: list[str] = Field(default_factory=list)


class Flow(BaseModel):
    name: str
    steps: list[str] = Field(default_factory=list)


class ArchitectureExplanation(BaseModel):
    summary: str
    layers: list[Layer] = Field(default_factory=list)
    key_flows: list[Flow] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    title: str
    detail: str


class FailureAnalysis(BaseModel):
    summary: str
    failure_mechanism: str
    predicted_symptoms: list[str] = Field(default_factory=list)
    user_impact: str = ""
    assumptions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_tests: list[Recommendation] = Field(default_factory=list)
    mitigations: list[Recommendation] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    evidence_used: list[str] = Field(default_factory=list)


class Discrepancy(BaseModel):
    test: str
    explanation: str


class ExperimentInterpretation(BaseModel):
    verdict_explanation: str
    agreements: list[str] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    next_test: str = ""
    caveats: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)


class ChatAnswer(BaseModel):
    answer: str
    cited_components: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class DiscrepancyItem(BaseModel):
    component: str
    issue: str
    severity: Literal["low", "medium", "high"] = "medium"


class MultimodalAuditResult(BaseModel):
    summary: str
    detected_components: list[str] = Field(default_factory=list)
    code_alignment: list[str] = Field(default_factory=list)
    architectural_drift: list[DiscrepancyItem] = Field(default_factory=list)
    failure_susceptibility: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class DynamicScenarioSpec(BaseModel):
    scenario_id: str
    label: str
    fault_description: str
    target_type: str
    suggested_target_id: str
    exceptions: list[str] = Field(default_factory=list)
    affected_hypotheses: list[str] = Field(default_factory=list)
    mitigation_strategy: str = ""


class NeuroSymbolicFailurePrediction(BaseModel):
    semantic_summary: str
    cascading_risk_score: float = 50.0
    runtime_vulnerabilities: list[str] = Field(default_factory=list)
    test_coverage_gap: list[str] = Field(default_factory=list)
    blast_radius_assessment: str = ""
    suggested_mitigations: list[Recommendation] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    evidence_used: list[str] = Field(default_factory=list)


class DiscrepancyAnalysis(BaseModel):
    explanation: str
    generated_test: str
    suggested_patch: str
    evidence_used: list[str] = Field(default_factory=list)


SCHEMAS = {
    "architecture": ArchitectureExplanation,
    "failure_analysis": FailureAnalysis,
    "experiment_interpretation": ExperimentInterpretation,
    "chat": ChatAnswer,
    "multimodal_audit": MultimodalAuditResult,
    "dynamic_scenario": DynamicScenarioSpec,
    "neuro_symbolic_prediction": NeuroSymbolicFailurePrediction,
    "discrepancy": DiscrepancyAnalysis,
}
T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _skeleton(schema: dict, defs: dict):
    if "$ref" in schema:
        return _skeleton(defs[schema["$ref"].split("/")[-1]], defs)
    if "enum" in schema:
        return "|".join(schema["enum"])
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return {k: _skeleton(v, defs) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [_skeleton(schema.get("items", {}), defs)]
    return t or "string"


def _schema_hint(model: type[BaseModel]) -> str:
    """A compact example skeleton of the expected JSON (much shorter than a full JSON Schema)."""
    schema = model.model_json_schema()
    return json.dumps(_skeleton(schema, schema.get("$defs", {})), separators=(",", ":"))


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences and stray prose)."""
    if not text:
        raise ValueError("empty response")
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    candidate = fence.group(1) if fence else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in response")
    return json.loads(candidate[start: end + 1])


def response_text(response) -> str:
    """Concatenate non-thought text parts of the first candidate."""
    parts = []
    for cand in (getattr(response, "candidates", None) or [])[:1]:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "thought", False):
                continue
            if getattr(part, "text", None):
                parts.append(part.text)
    return "".join(parts)


class GemmaService:
    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self.last_check: dict | None = None
        self.last_request: dict | None = None

    @property
    def model(self) -> str:
        return settings.gemma_model

    @property
    def configured(self) -> bool:
        return bool(settings.api_key)

    def _get_client(self):
        if not self.configured:
            raise AIError("not_configured", "No API key configured. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in backend/.env "
                                            "and restart the backend.", status=503)
        with self._lock:
            if self._client is None:
                from google import genai
                from google.genai import types

                self._client = genai.Client(api_key=settings.api_key,
                                            http_options=types.HttpOptions(timeout=settings.ai_timeout_s * 1000))
            return self._client

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "model": self.model,
            "provider": "Google Gen AI SDK (Gemini API)",
            "last_check": self.last_check,
            "last_request": self.last_request,
        }

    def check_model(self) -> dict:
        """Ask the API whether the configured model exists and is accessible with this key."""
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            client = self._get_client()
            info = client.models.get(model=self.model)
            self.last_check = {"available": True, "checked_at": checked_at,
                               "display_name": getattr(info, "display_name", None) or self.model}
        except AIError as exc:
            self.last_check = {"available": False, "checked_at": checked_at, "error": exc.message, "code": exc.code}
        except Exception as exc:  # SDK/network errors
            err = self._map_error(exc)
            self.last_check = {"available": False, "checked_at": checked_at, "error": err.message, "code": err.code}
        return self.last_check

    def _map_error(self, exc: Exception) -> AIError:
        try:
            from google.genai import errors as genai_errors
        except ImportError:  # pragma: no cover
            genai_errors = None
        code = getattr(exc, "code", None)
        msg = str(exc)
        if settings.api_key:
            msg = msg.replace(settings.api_key, "***")
        if genai_errors and isinstance(exc, genai_errors.APIError):
            if code == 429:
                return AIError("rate_limited", "Gemma rate limit or quota reached. Wait a minute and retry.", 429)
            if code in (401, 403):
                return AIError("unauthorized", "The API key was rejected or lacks access to this model.", 502)
            if code == 404:
                return AIError("model_unavailable", f"Model '{self.model}' is not available for this API key.", 502)
            if code == 504:
                return AIError("timeout", "Gemma exceeded the Gemini API deadline (it reasons before answering). "
                                          "Retry; the request usually succeeds on a second attempt.", 504)
            if code and code >= 500:
                return AIError("provider_error", f"The Gemini API returned a server error ({code}). DevDNA retried automatically, but the provider is temporarily overloaded. Please retry shortly.", 502)
            return AIError("provider_error", f"Gemini API error {code}: {msg[:300]}", 502)
        name = type(exc).__name__.lower()
        if "timeout" in name or "timeout" in msg.lower():
            return AIError("timeout", f"Gemma did not respond within {settings.ai_timeout_s}s.", 504)
        return AIError("provider_error", f"{type(exc).__name__}: {msg[:300]}", 502)

    def _call(self, contents: str | list) -> str:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.2,
                                             max_output_tokens=4096)
        # Streaming: Gemma reasons before answering, and a single non-streamed call can exceed the
        # API's 60 s deadline. A stream starts within seconds and is not cut off.
        max_attempts = 3
        last_error = None
        for attempt in range(max_attempts):
            text = []
            try:
                for chunk in client.models.generate_content_stream(model=self.model, contents=contents, config=config):
                    text.append(response_text(chunk))
                return "".join(text)
            except AIError:
                raise
            except Exception as exc:
                last_error = self._map_error(exc)
                # Retry transient provider errors (5xx, timeouts, rate limits)
                is_transient = (
                    last_error.code in ("provider_error", "timeout", "rate_limited")
                    and getattr(last_error, "status", 502) in (429, 500, 502, 503, 504)
                )
                if is_transient and attempt < max_attempts - 1:
                    sleep_s = (1.5 ** attempt) * 1.0
                    time.sleep(sleep_s)
                    continue
                raise last_error from None
        if last_error:
            raise last_error
        return ""

    def generate(self, task: str, prompt: str, allowed_refs: set[str]) -> tuple[BaseModel, dict]:
        schema = SCHEMAS[task]
        full_prompt = (f"{prompt}\n\nBe concise: at most 5 items per list and 1-3 sentences per text field. "
                       f"Return ONLY a JSON object with exactly this shape:\n{_schema_hint(schema)}")
        started = time.monotonic()
        attempts = 0
        last_error = ""
        text = ""
        while attempts < 2:
            attempts += 1
            try:
                text = self._call(full_prompt if attempts == 1 else
                                  f"{full_prompt}\n\nYour previous reply could not be parsed ({last_error}). "
                                  f"Reply again with ONLY the JSON object.")
            except AIError as exc:
                self.last_request = {"status": exc.code, "at": datetime.now(timezone.utc).isoformat(), "task": task}
                raise
            try:
                parsed = schema.model_validate(extract_json(text))
                break
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:300]
        else:
            self.last_request = {"status": "invalid_output", "at": datetime.now(timezone.utc).isoformat(), "task": task}
            raise AIError("invalid_output", f"Gemma returned output that did not match the expected schema "
                                            f"after {attempts} attempts: {last_error}", 502)
        latency = int((time.monotonic() - started) * 1000)
        cited = set(getattr(parsed, "evidence_used", []) or [])
        meta = {
            "model": self.model, "latency_ms": latency, "attempts": attempts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ungrounded_refs": sorted(cited - allowed_refs),
        }
        if hasattr(parsed, "evidence_used"):
            parsed.evidence_used = [r for r in parsed.evidence_used if r in allowed_refs]
        self.last_request = {"status": "ok", "at": meta["generated_at"], "task": task, "latency_ms": latency}
        return parsed, meta

    def generate_multimodal(
        self,
        task: str,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        allowed_refs: set[str] | None = None,
    ) -> tuple[BaseModel, dict]:
        """Multimodal reasoning with Gemma 4 using image bytes (diagram or error screenshot) + text prompt."""
        from google.genai import types

        schema = SCHEMAS[task]
        full_prompt = (
            f"{prompt}\n\n"
            f"Visually analyze this provided architecture diagram or screenshot alongside the repository evidence.\n"
            f"Be concise: at most 5 items per list and 1-3 sentences per text field.\n"
            f"Return ONLY a JSON object with exactly this shape:\n{_schema_hint(schema)}"
        )
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        contents = [image_part, full_prompt]

        started = time.monotonic()
        attempts = 0
        last_error = ""
        text = ""
        while attempts < 2:
            attempts += 1
            try:
                call_contents = contents if attempts == 1 else [
                    image_part,
                    f"{full_prompt}\n\nYour previous reply could not be parsed ({last_error}). "
                    f"Reply again with ONLY the JSON object."
                ]
                text = self._call(call_contents)
            except AIError as exc:
                self.last_request = {"status": exc.code, "at": datetime.now(timezone.utc).isoformat(), "task": task}
                raise
            try:
                parsed = schema.model_validate(extract_json(text))
                break
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:300]
        else:
            self.last_request = {"status": "invalid_output", "at": datetime.now(timezone.utc).isoformat(), "task": task}
            raise AIError("invalid_output", f"Gemma returned output that did not match the expected schema "
                                            f"after {attempts} attempts: {last_error}", 502)
        latency = int((time.monotonic() - started) * 1000)
        cited = set(getattr(parsed, "evidence_used", []) or [])
        refs = allowed_refs or set()
        meta = {
            "model": self.model,
            "latency_ms": latency,
            "attempts": attempts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "multimodal": True,
            "mime_type": mime_type,
            "image_size_bytes": len(image_bytes),
            "ungrounded_refs": sorted(cited - refs),
        }
        if hasattr(parsed, "evidence_used"):
            parsed.evidence_used = [r for r in parsed.evidence_used if r in refs]
        self.last_request = {"status": "ok", "at": meta["generated_at"], "task": task, "latency_ms": latency}
        return parsed, meta


gemma = GemmaService()

