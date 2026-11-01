"""Regulatory library: ingestion, metadata, versioning and withdrawal.

Documents can be added, superseded, withdrawn or (when never cited) removed
without changing the AI pipeline: retrieval always reads the active set from
the database and the vector index follows automatically.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import detect_injection, safe_filename
from app.models import EvidenceRecord, RegulatoryChunk, RegulatoryDocument
from app.schemas import BINDING_TYPES, DocumentStatus, DocumentType, RegulationMetadata
from app.services import audit
from app.services.document_processor import chunk_pages, detect_markdown, extract_pages, sha256_bytes, uses_ocr

log = get_logger("regulatory_library")


class LibraryError(ValueError):
    pass


class DocumentConflict(LibraryError):
    pass


def is_binding(doc: RegulatoryDocument) -> bool:
    return DocumentType(doc.document_type) in BINDING_TYPES and doc.jurisdiction != "international"


def ingest_file(db: Session, metadata: RegulationMetadata, path: Path, *, actor: str = "system",
                copy_to_library: bool = False, replace: bool = False) -> RegulatoryDocument:
    """Ingest one document. Idempotent for an unchanged file with the same id."""
    settings = get_settings()
    data = path.read_bytes()
    checksum = sha256_bytes(data)
    existing = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == metadata.document_id))
    if existing is not None:
        if existing.checksum == checksum and existing.version == metadata.version and not replace:
            return existing
        if not replace:
            raise DocumentConflict(
                f"Document '{metadata.document_id}' already exists (version {existing.version}). "
                "Ingest the new version under a new document_id with 'supersedes', or pass replace=True "
                "to correct the existing record.")
        if db.scalar(select(func.count()).select_from(EvidenceRecord)
                     .where(EvidenceRecord.document_id == existing.document_id)):
            raise DocumentConflict(
                f"Document '{metadata.document_id}' is cited by existing assessments and cannot be replaced; "
                "ingest a new version with 'supersedes' instead.")
        db.delete(existing)
        db.flush()

    if metadata.supersedes:
        old = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == metadata.supersedes))
        if old is None:
            raise LibraryError(f"Superseded document '{metadata.supersedes}' not found")

    stored = path
    if copy_to_library:
        dest_dir = settings.uploads_dir / "regulations"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stored = dest_dir / safe_filename(path.name)
        shutil.copyfile(path, stored)

    pages = extract_pages(path)
    chunks = chunk_pages(pages, settings.chunk_size_chars, settings.chunk_overlap_chars,
                         markdown=detect_markdown(path))
    if not chunks:
        raise LibraryError("No extractable text found in document (scanned PDFs need OCR first)")

    doc = RegulatoryDocument(
        document_id=metadata.document_id, title=metadata.title, short_title=metadata.short_title,
        issuing_authority=metadata.issuing_authority, jurisdiction=metadata.jurisdiction,
        document_type=metadata.document_type.value, content_form=metadata.content_form.value,
        publication_date=metadata.publication_date, effective_date=metadata.effective_date,
        version=metadata.version, source_url=metadata.source_url, source_url_note=metadata.source_url_note,
        applicable_sectors=metadata.applicable_sectors, date_last_verified=metadata.date_last_verified,
        legal_status=metadata.legal_status, status_verified=metadata.status_verified,
        status=DocumentStatus.active.value, supersedes=metadata.supersedes,
        official_counterpart=metadata.official_counterpart,
        extraction_method="ocr" if uses_ocr(path) else ("text_layer" if path.suffix.lower() == ".pdf" else "text_file"),
        file_path=str(stored), checksum=checksum)
    for c in chunks:
        doc.chunks.append(RegulatoryChunk(chunk_index=c.index, text=c.text, page=c.page, section=c.section,
                                          injection_flag=bool(detect_injection(c.text))))
    db.add(doc)
    db.flush()

    if metadata.supersedes:
        old = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == metadata.supersedes))
        prev = old.status
        old.status = DocumentStatus.superseded.value
        old.superseded_by = metadata.document_id
        audit.record(db, actor=actor, actor_type="human" if actor != "system" else "system",
                     action="regulation_superseded", entity_type="regulation", entity_id=old.document_id,
                     field="status", previous_value=prev, new_value="superseded",
                     comment=f"Superseded by {metadata.document_id}")
    audit.record(db, actor=actor, actor_type="human" if actor != "system" else "system",
                 action="regulation_ingested", entity_type="regulation", entity_id=doc.document_id,
                 new_value={"version": doc.version, "chunks": len(chunks), "checksum": checksum[:12]})
    log.info("Ingested %s (%d chunks)", doc.document_id, len(chunks))
    return doc


def ingest_manifest(db: Session, manifest_path: Path | None = None, actor: str = "system") -> list[RegulatoryDocument]:
    settings = get_settings()
    manifest_path = manifest_path or (settings.regulations_dir / "manifest.yaml")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    docs = []
    for entry in manifest["documents"]:
        entry = dict(entry)
        rel = entry.pop("file")
        meta = RegulationMetadata(**entry)
        try:
            docs.append(ingest_file(db, meta, manifest_path.parent / rel, actor=actor))
        except DocumentConflict:
            # A revised seed summary may replace its previous revision only if nothing cites it;
            # otherwise ingest_file refuses again and the revision must be added via 'supersedes'.
            docs.append(ingest_file(db, meta, manifest_path.parent / rel, actor=actor, replace=True))
    _relink_counterparts(db)
    db.commit()
    return docs


def _relink_counterparts(db: Session) -> None:
    """Restore summary -> official links after a summary has been re-ingested."""
    from app.services.crosswalk import crosswalk

    for summary_id, entry in crosswalk().items():
        summary = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == summary_id))
        official = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == entry["official"]))
        if summary is not None and official is not None and summary.official_counterpart is None:
            summary.official_counterpart = official.document_id


def set_status(db: Session, document_id: str, status: DocumentStatus, actor: str,
               superseded_by: str | None = None, comment: str | None = None) -> RegulatoryDocument:
    doc = get_document(db, document_id)
    prev = doc.status
    doc.status = status.value
    if status == DocumentStatus.superseded:
        if not superseded_by:
            raise LibraryError("superseded_by is required when marking a document as superseded")
        get_document(db, superseded_by)
        doc.superseded_by = superseded_by
    audit.record(db, actor=actor, actor_type="human", action=f"regulation_{status.value}",
                 entity_type="regulation", entity_id=document_id, field="status",
                 previous_value=prev, new_value=status.value, comment=comment)
    db.commit()
    return doc


def update_metadata(db: Session, document_id: str, changes: dict, actor: str) -> RegulatoryDocument:
    allowed = {"legal_status", "status_verified", "date_last_verified", "source_url", "source_url_note",
               "effective_date", "short_title", "applicable_sectors", "official_counterpart"}
    doc = get_document(db, document_id)
    for key, value in changes.items():
        if key not in allowed:
            raise LibraryError(f"Field '{key}' cannot be edited; ingest a new version instead")
        prev = getattr(doc, key)
        setattr(doc, key, value)
        audit.record(db, actor=actor, actor_type="human", action="regulation_metadata_updated",
                     entity_type="regulation", entity_id=document_id, field=key,
                     previous_value=prev, new_value=value)
    db.commit()
    return doc


def hard_delete(db: Session, document_id: str, actor: str) -> None:
    doc = get_document(db, document_id)
    cited = db.scalar(select(func.count()).select_from(EvidenceRecord).where(EvidenceRecord.document_id == document_id))
    if cited:
        raise DocumentConflict(f"Document '{document_id}' is cited by {cited} evidence references; withdraw it instead")
    audit.record(db, actor=actor, actor_type="human", action="regulation_deleted", entity_type="regulation",
                 entity_id=document_id, previous_value={"title": doc.title, "version": doc.version})
    db.delete(doc)
    db.commit()


def get_document(db: Session, document_id: str) -> RegulatoryDocument:
    doc = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == document_id))
    if doc is None:
        raise LibraryError(f"Regulatory document '{document_id}' not found")
    return doc


def knowledge_base_version(db: Session) -> str:
    """Deterministic identifier of the active document set used for an assessment."""
    docs = db.scalars(select(RegulatoryDocument).where(RegulatoryDocument.status == "active")
                      .order_by(RegulatoryDocument.document_id)).all()
    h = hashlib.sha1()
    for d in docs:
        h.update(f"{d.document_id}|{d.version}|{d.checksum}".encode())
    return f"kb-{len(docs)}docs-{h.hexdigest()[:10]}"


def library_stats(db: Session) -> dict:
    docs = db.scalars(select(RegulatoryDocument)).all()
    by_j: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_form: dict[str, int] = {}
    for d in docs:
        if d.status == "active":
            by_j[d.jurisdiction] = by_j.get(d.jurisdiction, 0) + 1
        by_status[d.status] = by_status.get(d.status, 0) + 1
        by_form[d.content_form] = by_form.get(d.content_form, 0) + 1
    chunks = db.scalar(select(func.count()).select_from(RegulatoryChunk)) or 0
    return {"documents": len(docs), "chunks": chunks, "active_by_jurisdiction": by_j,
            "by_status": by_status, "by_content_form": by_form,
            "knowledge_base_version": knowledge_base_version(db),
            "generated_at": datetime.now(timezone.utc).isoformat()}
