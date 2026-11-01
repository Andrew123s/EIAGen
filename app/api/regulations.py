"""Regulatory library endpoints."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import UploadValidationError, require_admin_key, validate_upload
from app.database import get_db
from app.models import RegulatoryChunk, RegulatoryDocument
from app.schemas import DocumentStatus, RegulationMetadata, RegulationOut, RegulationSearchHit, RegulationSearchRequest
from app.services.jurisdictions import is_supported
from app.services.regulatory_library import (
    DocumentConflict,
    LibraryError,
    get_document,
    hard_delete,
    ingest_file,
    ingest_manifest,
    is_binding,
    set_status,
    update_metadata,
)
from app.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/regulations", tags=["regulations"])


def to_out(d: RegulatoryDocument) -> RegulationOut:
    return RegulationOut(id=d.id, document_id=d.document_id, title=d.title, short_title=d.short_title,
                         issuing_authority=d.issuing_authority, jurisdiction=d.jurisdiction, document_type=d.document_type,
                         content_form=d.content_form, binding=is_binding(d), publication_date=d.publication_date,
                         effective_date=d.effective_date, version=d.version, source_url=d.source_url,
                         applicable_sectors=d.applicable_sectors or [], date_last_verified=d.date_last_verified,
                         legal_status=d.legal_status, status_verified=d.status_verified, status=d.status,
                         superseded_by=d.superseded_by, chunk_count=len(d.chunks), checksum=d.checksum,
                         ingested_at=d.ingested_at)


def _lib_error(exc: LibraryError) -> HTTPException:
    if isinstance(exc, DocumentConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if "not found" in str(exc):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(422, str(exc))


@router.get("", response_model=list[RegulationOut])
def list_regulations(jurisdiction: str | None = None, status_filter: str | None = None,
                     db: Session = Depends(get_db)) -> list[RegulationOut]:
    q = select(RegulatoryDocument).order_by(RegulatoryDocument.jurisdiction, RegulatoryDocument.document_id)
    if jurisdiction:
        q = q.where(RegulatoryDocument.jurisdiction == jurisdiction)
    if status_filter:
        q = q.where(RegulatoryDocument.status == status_filter)
    return [to_out(d) for d in db.scalars(q)]


@router.post("/ingest", response_model=RegulationOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin_key)])
async def ingest_regulation(file: UploadFile = File(...), metadata: str = Form(...), actor: str = Form("administrator"),
                            db: Session = Depends(get_db)) -> RegulationOut:
    try:
        meta = RegulationMetadata.model_validate(json.loads(metadata))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, f"Invalid metadata: {exc}") from exc
    if not is_supported(meta.jurisdiction) and meta.jurisdiction != "international":
        raise HTTPException(422, f"Unknown jurisdiction collection '{meta.jurisdiction}'")
    settings = get_settings()
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        ext = validate_upload(file.filename or "", content)
    except UploadValidationError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE if "not allowed" in str(exc) else
                            status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{meta.document_id}{ext}"
        path.write_bytes(content)
        try:
            doc = ingest_file(db, meta, path, actor=actor, copy_to_library=True)
            db.commit()
        except LibraryError as exc:
            db.rollback()
            raise _lib_error(exc) from exc
    db.refresh(doc)
    return to_out(doc)


@router.post("/ingest-manifest", dependencies=[Depends(require_admin_key)])
def ingest_seed_manifest(db: Session = Depends(get_db)) -> dict:
    try:
        docs = ingest_manifest(db)
    except LibraryError as exc:
        db.rollback()
        raise _lib_error(exc) from exc
    return {"documents": [d.document_id for d in docs]}


@router.post("/search", response_model=list[RegulationSearchHit])
def search(req: RegulationSearchRequest, db: Session = Depends(get_db)) -> list[RegulationSearchHit]:
    if req.jurisdiction and not is_supported(req.jurisdiction):
        raise HTTPException(422, f"Unsupported jurisdiction '{req.jurisdiction}'")
    hits = RetrievalService(db).search(req.query, req.jurisdiction or "*", req.include_related, req.top_k, min_score=0.0)
    return [RegulationSearchHit(chunk_id=h.chunk_id, document_id=h.document_id, title=h.title, section=h.section,
                                page=h.page, jurisdiction=h.jurisdiction, relationship=h.relationship,
                                document_type=h.document_type, content_form=h.content_form, score=h.score, text=h.text)
            for h in hits]


@router.get("/{document_id}", response_model=RegulationOut)
def get_regulation(document_id: str, db: Session = Depends(get_db)) -> RegulationOut:
    try:
        return to_out(get_document(db, document_id))
    except LibraryError as exc:
        raise _lib_error(exc) from exc


@router.get("/{document_id}/chunks")
def get_chunks(document_id: str, db: Session = Depends(get_db)) -> list[dict]:
    try:
        doc = get_document(db, document_id)
    except LibraryError as exc:
        raise _lib_error(exc) from exc
    return [{"chunk_id": c.id, "index": c.chunk_index, "section": c.section, "page": c.page, "text": c.text,
             "injection_flag": c.injection_flag}
            for c in db.scalars(select(RegulatoryChunk).where(RegulatoryChunk.regulation_id == doc.id)
                                .order_by(RegulatoryChunk.chunk_index))]


class MetadataPatch(BaseModel):
    actor: str
    changes: dict


class StatusChange(BaseModel):
    actor: str
    status: DocumentStatus
    superseded_by: str | None = None
    comment: str | None = None


@router.patch("/{document_id}", response_model=RegulationOut, dependencies=[Depends(require_admin_key)])
def patch_regulation(document_id: str, patch: MetadataPatch, db: Session = Depends(get_db)) -> RegulationOut:
    try:
        return to_out(update_metadata(db, document_id, patch.changes, patch.actor))
    except LibraryError as exc:
        db.rollback()
        raise _lib_error(exc) from exc


@router.post("/{document_id}/status", response_model=RegulationOut, dependencies=[Depends(require_admin_key)])
def change_status(document_id: str, change: StatusChange, db: Session = Depends(get_db)) -> RegulationOut:
    try:
        return to_out(set_status(db, document_id, change.status, change.actor, change.superseded_by, change.comment))
    except LibraryError as exc:
        db.rollback()
        raise _lib_error(exc) from exc


@router.delete("/{document_id}", dependencies=[Depends(require_admin_key)])
def delete_regulation(document_id: str, hard: bool = False, actor: str = "administrator",
                      db: Session = Depends(get_db)) -> dict:
    """Withdraw (default, keeps history) or hard-delete a never-cited document."""
    try:
        if hard:
            hard_delete(db, document_id, actor)
            return {"document_id": document_id, "deleted": True}
        doc = set_status(db, document_id, DocumentStatus.withdrawn, actor, comment="Withdrawn via DELETE")
        return {"document_id": document_id, "status": doc.status, "deleted": False,
                "note": "Document withdrawn: excluded from retrieval, retained for traceability of earlier citations."}
    except LibraryError as exc:
        db.rollback()
        raise _lib_error(exc) from exc
