from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.project import utcnow


class HumanReview(Base):
    """A reviewer action (comment, request changes, approve, issue...)."""

    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    reviewer_name: Mapped[str] = mapped_column(String(120))
    reviewer_role: Mapped[str] = mapped_column(String(120))
    reviewer_organisation: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(40))
    previous_state: Mapped[str] = mapped_column(String(40))
    new_state: Mapped[str] = mapped_column(String(40))
    comments: Mapped[str | None] = mapped_column(Text)
    unresolved_issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Append-only audit trail of state changes and finding edits."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    screening_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(120))
    actor_type: Mapped[str] = mapped_column(String(20))   # human | system
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    field: Mapped[str | None] = mapped_column(String(80))
    previous_value: Mapped[dict | list | str | None] = mapped_column(JSON)
    new_value: Mapped[dict | list | str | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
