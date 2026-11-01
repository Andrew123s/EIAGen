"""Screening runs and their findings.

Each screening run is an immutable assessment version. Re-running a screening
creates a new version; reviewed runs are never overwritten. Findings are stored
both as validated JSON on the run and as normalised rows that reviewers update
(with every change written to the audit log).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.project import Project, utcnow


class ScreeningRun(Base):
    __tablename__ = "screening_runs"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_run_project_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running")
    review_state: Mapped[str] = mapped_column(String(40), default="draft")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    knowledge_base_version: Mapped[str | None] = mapped_column(String(60))
    llm_provider: Mapped[str | None] = mapped_column(String(60))
    taxonomy_version: Mapped[str | None] = mapped_column(String(40))
    rules_version: Mapped[str | None] = mapped_column(String(40))
    profile_json: Mapped[dict | None] = mapped_column(JSON)
    assessment_json: Mapped[dict | None] = mapped_column(JSON)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    investigations_json: Mapped[list | None] = mapped_column(JSON)
    narrative_json: Mapped[dict | None] = mapped_column(JSON)
    stages_json: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="screening_runs")
    activities: Mapped[list["ActivityRecord"]] = relationship(cascade="all, delete-orphan")
    impacts: Mapped[list["ImpactRecord"]] = relationship(cascade="all, delete-orphan")
    requirements: Mapped[list["RequirementRecord"]] = relationship(cascade="all, delete-orphan")
    mitigations: Mapped[list["MitigationRecord"]] = relationship(cascade="all, delete-orphan")
    missing_items: Mapped[list["MissingInformationRecord"]] = relationship(cascade="all, delete-orphan")
    evidence: Mapped[list["EvidenceRecord"]] = relationship(cascade="all, delete-orphan")
    tool_executions: Mapped[list["ToolExecution"]] = relationship(cascade="all, delete-orphan")


class _FindingMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ActivityRecord(_FindingMixin, Base):
    __tablename__ = "project_activities"
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    activity_key: Mapped[str] = mapped_column(String(60))


class ImpactRecord(_FindingMixin, Base):
    __tablename__ = "environmental_impacts"
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    component: Mapped[str] = mapped_column(String(60))
    risk_priority: Mapped[str | None] = mapped_column(String(10))


class RequirementRecord(_FindingMixin, Base):
    __tablename__ = "regulatory_requirements"
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(String(80))
    applicability: Mapped[str] = mapped_column(String(40))


class MitigationRecord(_FindingMixin, Base):
    __tablename__ = "mitigation_measures"
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    impact_id: Mapped[str] = mapped_column(String(40))


class MissingInformationRecord(_FindingMixin, Base):
    __tablename__ = "missing_information"
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    importance: Mapped[str] = mapped_column(String(20))


class EvidenceRecord(Base):
    __tablename__ = "evidence_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    citation_key: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[str] = mapped_column(String(80))
    chunk_id: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    passage: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict] = mapped_column(JSON)


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    screening_run_id: Mapped[int | None] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    stage: Mapped[str] = mapped_column(String(60))
    tool_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
