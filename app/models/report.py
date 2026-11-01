from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.project import utcnow


class Report(Base):
    """A generated preliminary screening report. Each generation is a new version."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    review_state_at_generation: Mapped[str] = mapped_column(String(40))
    knowledge_base_version: Mapped[str | None] = mapped_column(String(60))
    content_json: Mapped[dict] = mapped_column(JSON)
    docx_path: Mapped[str | None] = mapped_column(String(500))
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    markdown_path: Mapped[str | None] = mapped_column(String(500))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
