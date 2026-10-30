"""Persistent records: projects, analysis runs, graph, findings, predictions, experiments, reports."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(20))  # sample | upload | local
    source_path: Mapped[str] = mapped_column(Text)
    trusted: Mapped[bool] = mapped_column(default=False)  # only the bundled sample is trusted
    ingest_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    latest_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ast_owner: Mapped[str | None] = mapped_column(String(39), nullable=True)
    ast_repo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ast_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    stage: Mapped[str] = mapped_column(String(60), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
    components: Mapped[list["Component"]] = relationship(cascade="all, delete-orphan")
    edges: Mapped[list["Edge"]] = relationship(cascade="all, delete-orphan")
    findings: Mapped[list["Finding"]] = relationship(cascade="all, delete-orphan")


class Component(Base):
    __tablename__ = "components"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    id: Mapped[str] = mapped_column(String(400))  # stable component id, e.g. "func:app.services.orders.create_order"
    type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300))
    file: Mapped[str | None] = mapped_column(Text, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class Edge(Base):
    __tablename__ = "edges"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    id: Mapped[str] = mapped_column(String(900))
    source: Mapped[str] = mapped_column(String(400))
    target: Mapped[str] = mapped_column(String(400))
    type: Mapped[str] = mapped_column(String(30))
    classification: Mapped[str] = mapped_column(String(20))  # confirmed | inferred | unknown
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    call_sites: Mapped[list] = mapped_column(JSON, default=list)  # enclosing try/except handlers per call site


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    rule: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10))
    component_id: Mapped[str | None] = mapped_column(String(400), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[str] = mapped_column(String(10))
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    mitigation: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(String(20), default="open")
    owner: Mapped[str] = mapped_column(String(500), default="Unassigned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FindingSuppression(Base):
    __tablename__ = "finding_suppressions"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    finding_id: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class FindingSuppressionAudit(Base):
    __tablename__ = "finding_suppression_audit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    finding_id: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(20))
    owner: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    scenario: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(400))
    impact: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"))
    status: Mapped[str] = mapped_column(String(30), default="prediction_only")
    mode: Mapped[str] = mapped_column(String(30), default="")  # local_harness | docker | blocked
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    comparison: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_interpretation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    prediction: Mapped[Prediction] = relationship()


class PredictionOutcome(Base):
    """Persisted outcome per test from experiment comparisons for calibration."""
    __tablename__ = "prediction_outcomes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=True, index=True)
    prediction_id: Mapped[str | None] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    scenario: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(400))
    test_nodeid: Mapped[str] = mapped_column(String(500))

    # Pattern features
    guard_present: Mapped[bool] = mapped_column(default=False, index=True)
    test_mocked: Mapped[bool] = mapped_column(default=False, index=True)
    certainty: Mapped[str] = mapped_column(String(20), default="definite", index=True)
    routes_affected: Mapped[int] = mapped_column(Integer, default=0)

    # Outcomes
    predicted_outcome: Mapped[str] = mapped_column(String(20))
    observed_outcome: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[str] = mapped_column(String(30))
    broke: Mapped[bool] = mapped_column(default=False, index=True)

    pattern_key: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AIInteraction(Base):
    """Audit log of every Gemma request: what evidence was sent and what came back."""

    __tablename__ = "ai_interactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str | None] = mapped_column(String(400), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # ok | error | invalid_output
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReportRecord(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # project | experiment
    subject_id: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
