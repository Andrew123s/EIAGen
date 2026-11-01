"""Citation creation and verification.

Every citation maps to a real stored passage. Verification re-reads the
database and checks: document exists, source identifier, chunk belongs to the
document, page, section, the passage is verbatim in the stored text,
jurisdiction is admissible for the project, document version, and status.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectDocument, RegulatoryChunk, RegulatoryDocument
from app.schemas import CitationValidationResult, ContentForm, DocumentType, EvidenceReference
from app.services.jurisdictions import collection_relationships
from app.services.retrieval_service import RetrievedPassage

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def excerpt(text: str, limit: int = 600) -> str:
    """A verbatim excerpt (prefix) of a passage, cut at a sentence boundary when possible."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return cut[:dot + 1] if dot > limit // 2 else cut


class CitationRegistry:
    """Assigns stable citation keys within one assessment."""

    def __init__(self) -> None:
        self.by_chunk: dict[int, EvidenceReference] = {}
        self.items: list[EvidenceReference] = []
        self.counters: dict[str, int] = {}

    def _next_key(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]:02d}"

    @staticmethod
    def prefix_for(doc_type: str, jurisdiction: str) -> str:
        if jurisdiction == "international":
            return "INTL"
        if doc_type in (DocumentType.guideline.value, DocumentType.technical_standard.value,
                        DocumentType.non_binding_recommendation.value):
            return "GUIDE"
        return "REG"

    def cite_passage(self, p: RetrievedPassage, doc: RegulatoryDocument) -> EvidenceReference:
        if p.chunk_id in self.by_chunk:
            return self.by_chunk[p.chunk_id]
        ref = EvidenceReference(
            citation_key=self._next_key(self.prefix_for(p.document_type, p.jurisdiction)),
            source_type="regulatory", document_id=p.document_id, chunk_id=p.chunk_id, title=p.title,
            section=p.section, page=p.page, passage=excerpt(p.text), jurisdiction=p.jurisdiction,
            version=doc.version, source_url=doc.source_url, content_form=ContentForm(doc.content_form),
            document_type=DocumentType(doc.document_type), relevance_score=p.score)
        self.by_chunk[p.chunk_id] = ref
        self.items.append(ref)
        return ref

    def cite_project_document(self, doc: ProjectDocument, page: int | None, passage: str) -> EvidenceReference:
        for existing in self.items:
            if existing.source_type == "project_document" and existing.document_id == f"projdoc-{doc.id}" \
                    and existing.page == page:
                return existing
        ref = EvidenceReference(
            citation_key=self._next_key("DOC"), source_type="project_document", document_id=f"projdoc-{doc.id}",
            title=f"Uploaded project document: {doc.original_filename}", page=page, passage=passage)
        self.items.append(ref)
        return ref


def validate_citation(db: Session, ref: EvidenceReference, project_jurisdiction: str,
                      project_id: int | None = None) -> CitationValidationResult:
    checks: dict[str, bool] = {}
    issues: list[str] = []
    if ref.source_type == "project_document":
        doc_id = int(ref.document_id.split("-", 1)[1]) if ref.document_id.startswith("projdoc-") else -1
        doc = db.get(ProjectDocument, doc_id)
        checks["document_exists"] = doc is not None
        checks["belongs_to_project"] = bool(doc and (project_id is None or doc.project_id == project_id))
        page_text = ""
        if doc is not None:
            pages = doc.pages or [{"page": None, "text": doc.extracted_text or ""}]
            match = [p for p in pages if p.get("page") == ref.page]
            checks["page_valid"] = bool(match)
            page_text = match[0]["text"] if match else ""
        else:
            checks["page_valid"] = False
        checks["passage_verbatim"] = bool(ref.passage) and _norm(ref.passage) in _norm(page_text)
    else:
        doc = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == ref.document_id))
        chunk = db.get(RegulatoryChunk, ref.chunk_id) if ref.chunk_id is not None else None
        checks["document_exists"] = doc is not None
        checks["chunk_belongs_to_document"] = bool(doc and chunk and chunk.regulation_id == doc.id)
        checks["page_matches"] = bool(chunk) and chunk.page == ref.page
        checks["section_matches"] = bool(chunk) and (chunk.section or None) == (ref.section or None)
        checks["passage_verbatim"] = bool(chunk) and bool(ref.passage) and _norm(ref.passage) in _norm(chunk.text)
        checks["jurisdiction_admissible"] = bool(doc) and doc.jurisdiction == ref.jurisdiction and \
            doc.jurisdiction in collection_relationships(project_jurisdiction)
        checks["version_matches"] = bool(doc) and doc.version == ref.version
        checks["document_active"] = bool(doc) and doc.status == "active"
        if doc is not None and doc.content_form == ContentForm.curated_summary.value:
            issues.append("Passage is from a curated summary, not the official text - verify against the official source")
        if doc is not None and doc.extraction_method == "ocr":
            issues.append("Passage text was obtained by OCR from a scanned copy - check it against the page image")
        if doc is not None and not doc.status_verified:
            issues.append(f"Legal status not verified: {doc.legal_status or 'unknown'}")
    failed = [k for k, ok in checks.items() if not ok]
    issues = [f"Check failed: {k}" for k in failed] + issues
    return CitationValidationResult(citation_key=ref.citation_key, valid=not failed, checks=checks, issues=issues)
