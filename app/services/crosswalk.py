"""Link curated-summary passages to the official provisions they paraphrase.

Anchors (data/regulations/crosswalk.yaml) are verbatim phrases from the official
text. Resolution is deterministic: a passage whose section heading contains the
anchor wins; otherwise the passage containing the anchor with the most text
after it (so a body passage beats a table-of-contents line).
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import RegulatoryChunk, RegulatoryDocument
from app.services.retrieval_service import RetrievedPassage


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


@lru_cache(maxsize=1)
def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh).get("crosswalk", {})
    except FileNotFoundError:
        return {}


def crosswalk() -> dict:
    return _load(str(get_settings().regulations_dir / "crosswalk.yaml"))


def anchor_for(summary_document_id: str, section: str | None) -> tuple[str, str] | None:
    entry = crosswalk().get(summary_document_id)
    if not entry or not section:
        return None
    anchor = entry.get("sections", {}).get(section)
    return (entry["official"], anchor) if anchor else None


def _anchor_regex(anchor: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in anchor.split()]
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def resolve_anchor(db: Session, official_document_id: str, anchor: str) -> RetrievedPassage | None:
    doc = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == official_document_id))
    if doc is None or doc.status != "active":
        return None
    rx = _anchor_regex(anchor)
    chunks = db.scalars(select(RegulatoryChunk).where(RegulatoryChunk.regulation_id == doc.id)
                        .order_by(RegulatoryChunk.chunk_index)).all()
    best, start = None, 0
    for c in chunks:
        if c.section and rx.search(c.section) and len(c.text) >= 80:
            best, start = c, 0
            break
    if best is None:
        best_after = -1
        for c in chunks:
            m = rx.search(c.text)
            if m and len(c.text) - m.start() > best_after:
                best, start, best_after = c, m.start(), len(c.text) - m.start()
    if best is None:
        return None
    window = best.text[start:start + 700]
    return RetrievedPassage(chunk_id=best.id, document_id=doc.document_id, title=doc.title, section=best.section,
                            page=best.page, jurisdiction=doc.jurisdiction, relationship="primary",
                            document_type=doc.document_type, content_form=doc.content_form, text=window,
                            score=1.0, dense_score=0.0, lexical_score=1.0)


def official_passage_for(db: Session, summary_document_id: str, section: str | None) -> RetrievedPassage | None:
    found = anchor_for(summary_document_id, section)
    if found is None:
        return None
    return resolve_anchor(db, *found)
