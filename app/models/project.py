from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    developer: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(String(60))
    phase: Mapped[str | None] = mapped_column(String(60))
    objectives: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(300))
    jurisdiction: Mapped[str] = mapped_column(String(40), index=True)
    region: Mapped[str | None] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    characteristics: Mapped[dict] = mapped_column(JSON, default=dict)
    environmental_setting: Mapped[str | None] = mapped_column(Text)
    review_state: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    # Latest analysed (and possibly user-corrected) profile, used as screening input.
    profile_json: Mapped[dict | None] = mapped_column(JSON)
    profile_corrected_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    documents: Mapped[list["ProjectDocument"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectDocument.id")
    screening_runs: Mapped[list["ScreeningRun"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan", order_by="ScreeningRun.version")


class ProjectDocument(Base):
    __tablename__ = "project_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    document_category: Mapped[str] = mapped_column(String(60), default="project_description")
    extracted_text: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list | None] = mapped_column(JSON)   # [{"page": 1, "text": "..."}]
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    injection_flags: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="documents")
