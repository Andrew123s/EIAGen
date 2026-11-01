"""Hybrid retrieval over the regulatory knowledge base.

Dense (FAISS) and lexical (BM25) scores are combined into one relevance score
in [0, 1]:  score = 0.6 * dense_cosine + 0.4 * bm25 / max_bm25  (hybrid mode).
Results are filtered by jurisdiction collection and document status *before*
ranking, so a passage from another jurisdiction can never be returned as if it
applied to the selected jurisdiction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import RegulatoryChunk, RegulatoryDocument
from app.services.embedding_service import get_embedding_service
from app.services.jurisdictions import collection_relationships
from app.services.vector_store import VectorStore, fingerprint

log = get_logger("retrieval")

_TOKEN_RE = re.compile(r"[a-z0-9äöüß]+")
_STOP = {"the", "of", "and", "or", "to", "a", "an", "in", "on", "for", "by", "with", "is", "are",
         "be", "as", "at", "from", "that", "this", "which", "where", "such", "shall", "may", "any"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass
class RetrievedPassage:
    chunk_id: int
    document_id: str
    title: str
    section: str | None
    page: int | None
    jurisdiction: str
    relationship: str
    document_type: str
    content_form: str
    text: str
    score: float
    dense_score: float
    lexical_score: float


class _IndexCache:
    store: VectorStore | None = None


@dataclass(frozen=True)
class _Row:
    id: int
    text: str
    section: str | None
    page: int | None
    document_id: str
    title: str
    jurisdiction: str
    document_type: str
    content_form: str


class _CorpusCache:
    """Active passages and BM25 models, reused until the active chunk set changes."""
    key: str | None = None
    rows: list[_Row] = []
    bm25: dict = {}


def _active_rows(db: Session) -> list[_Row]:
    ids = db.scalars(select(RegulatoryChunk.id)
                     .join(RegulatoryDocument, RegulatoryChunk.regulation_id == RegulatoryDocument.id)
                     .where(RegulatoryDocument.status == "active").order_by(RegulatoryChunk.id)).all()
    key = fingerprint(list(ids), "corpus") + f":{get_settings().database_url}"
    if _CorpusCache.key != key:
        result = db.execute(
            select(RegulatoryChunk.id, RegulatoryChunk.text, RegulatoryChunk.section, RegulatoryChunk.page,
                   RegulatoryDocument.document_id, RegulatoryDocument.title, RegulatoryDocument.jurisdiction,
                   RegulatoryDocument.document_type, RegulatoryDocument.content_form)
            .join(RegulatoryDocument, RegulatoryChunk.regulation_id == RegulatoryDocument.id)
            .where(RegulatoryDocument.status == "active").order_by(RegulatoryChunk.id))
        _CorpusCache.rows = [_Row(*r) for r in result]
        _CorpusCache.bm25 = {}
        _CorpusCache.key = key
    return _CorpusCache.rows


def ensure_index(db: Session, rows: list[_Row] | None = None) -> VectorStore:
    """Return a vector index consistent with the current active chunk set."""
    settings = get_settings()
    rows = rows if rows is not None else _active_rows(db)
    embedder = get_embedding_service()
    fp = fingerprint([r.id for r in rows], embedder.name)
    store = _IndexCache.store
    if store is None or store.directory != settings.vector_store_dir:
        store = VectorStore(settings.vector_store_dir)
        store.load()
        _IndexCache.store = store
    if store.fingerprint != fp:
        log.info("Building vector index for %d chunks (%s)", len(rows), embedder.name)
        texts = [f"{r.title}. {r.section or ''}. {r.text}" for r in rows]
        vectors = embedder.embed_documents(texts) if texts else np.zeros((0, 1), dtype="float32")
        store.build([r.id for r in rows], vectors, fp)
    return store


def reset_index_cache() -> None:
    _IndexCache.store = None
    _CorpusCache.key = None
    _CorpusCache.rows = []
    _CorpusCache.bm25 = {}


def _bm25_for(collections: frozenset[str], allowed: dict[int, _Row]):
    """BM25 over one collection set, cached for the current corpus."""
    cached = _CorpusCache.bm25.get(collections)
    if cached is None:
        from rank_bm25 import BM25Okapi

        ids = list(allowed)
        cached = (ids, BM25Okapi([tokenize(f"{allowed[i].title} {allowed[i].section or ''} {allowed[i].text}")
                                  for i in ids]))
        _CorpusCache.bm25[collections] = cached
    return cached


class RetrievalService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def search(self, query: str, jurisdiction: str, include_related: bool = True,
               top_k: int | None = None, min_score: float | None = None) -> list[RetrievedPassage]:
        top_k = top_k or self.settings.top_k
        min_score = self.settings.min_relevance if min_score is None else min_score
        relationships = collection_relationships(jurisdiction, include_related)
        rows = _active_rows(self.db)
        if not rows:
            return []
        store = ensure_index(self.db, rows)
        allowed = {r.id: r for r in rows if r.jurisdiction in relationships}
        if not allowed:
            return []

        embedder = get_embedding_service()
        qvec = embedder.embed_query(query)
        dense = {cid: max(0.0, s) for cid, s in store.search(qvec, store.size) if cid in allowed}

        lexical: dict[int, float] = {}
        if self.settings.hybrid:
            ids, bm25 = _bm25_for(frozenset(relationships), allowed)
            raw = bm25.get_scores(tokenize(query))
            top = float(max(raw)) if len(raw) and max(raw) > 0 else 0.0
            if top > 0:
                lexical = {cid: float(s) / top for cid, s in zip(ids, raw)}

        results: list[RetrievedPassage] = []
        for cid, row in allowed.items():
            d = dense.get(cid, 0.0)
            lx = lexical.get(cid, 0.0)
            score = 0.6 * d + 0.4 * lx if self.settings.hybrid else d
            if score < min_score:
                continue
            results.append(RetrievedPassage(
                chunk_id=cid, document_id=row.document_id, title=row.title, section=row.section,
                page=row.page, jurisdiction=row.jurisdiction, relationship=relationships[row.jurisdiction],
                document_type=row.document_type, content_form=row.content_form, text=row.text,
                score=round(score, 4), dense_score=round(d, 4), lexical_score=round(lx, 4)))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
