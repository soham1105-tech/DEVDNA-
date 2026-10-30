"""Pydantic request models (validated by FastAPI)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScenarioName = str


class LocalProjectIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=200)
    github_url: str | None = Field(default=None, max_length=500)
    github_branch: str | None = Field(default=None, max_length=255)


class GitHubProjectIn(BaseModel):
    github_url: str = Field(min_length=1, max_length=500)
    github_branch: str = Field(default="main", min_length=1, max_length=255)


class PredictionIn(BaseModel):
    scenario: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=400)
    custom_spec: dict | None = Field(default=None, description="Optional custom dynamic failure scenario specification.")
    enable_ai_simulation: bool = Field(default=True, description="When true, synthesizes deep neuro-symbolic failure simulation.")


class AIRequest(BaseModel):
    consent: bool = Field(default=False, description="Must be true to send the evidence package to Gemma.")
    preview: bool = Field(default=False, description="Return the exact evidence package without calling the model.")


class DynamicScenarioIn(AIRequest):
    prompt: str = Field(min_length=3, max_length=2000, description="Natural language description of custom failure to simulate.")
    target_id: str | None = Field(default=None, max_length=400, description="Optional component target id.")


class MultimodalAuditIn(AIRequest):
    diagram_type: str = Field(default="architecture_diagram", max_length=100)
    notes: str = Field(default="", max_length=2000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(AIRequest):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


class ExperimentIn(BaseModel):
    approved: bool = Field(description="Explicit user approval to execute the experiment.")


class FindingPatch(BaseModel):
    review_status: Literal["open", "acknowledged", "resolved", "false_positive"]


class FindingSuppressionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    owner: str = Field(min_length=1, max_length=500)
    expires_at: datetime
