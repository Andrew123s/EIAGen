from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.project import utcnow


class RegulatoryDocument(Base):
    """A regulatory publication (legislation, regulation, guideline, standard...).

    Documents are never hard-deleted by the normal workflow: superseded or
    withdrawn documents stay in the database so that earlier citations remain
    traceable, but they are excluded from retrieval.
    """

    __tablename__ = "regulatory_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(400))
    short_title: Mapped[str | None] = mapped_column(String(120))
    issuing_authority: Mapped[str] = mapped_column(String(300))
    jurisdiction: Mapped[str] = mapped_column(String(40), index=True)
    document_type: Mapped[str] = mapped_column(String(40))
    content_form: Mapped[str] = mapped_column(String(30), default="official_text")
    publication_date: Mapped[str | None] = mapped_column(String(40))
    effective_date: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_url_note: Mapped[str | None] = mapped_column(Text)
    applicable_sectors: Mapped[list] = mapped_column(JSON, default=list)
    date_last_verified: Mapped[str | None] = mapped_column(String(40))
    legal_status: Mapped[str | None] = mapped_column(Text)
    status_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    superseded_by: Mapped[str | None] = mapped_column(String(80))
    supersedes: Mapped[str | None] = mapped_column(String(80))
    # For curated summaries: the official document whose provisions the summary paraphrases.
    official_counterpart: Mapped[str | None] = mapped_column(String(80))
    extraction_method: Mapped[str] = mapped_column(String(20), default="text_layer")   # text_layer | ocr | text_file
    file_path: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks: Mapped[list["RegulatoryChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="RegulatoryChunk.chunk_index")


class RegulatoryChunk(Base):
    __tablename__ = "regulatory_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulatory_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    document: Mapped[RegulatoryDocument] = relationship(back_populates="chunks")
