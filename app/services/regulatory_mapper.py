"""Map project activities and potential impacts to retrieved regulatory provisions.

Retrieval is *not* legal interpretation. For each retrieved provision the
mapper records what it may require, which activities it relates to, and an
applicability status from a controlled vocabulary:

* potentially_applicable  - binding instrument of the project's jurisdiction (or a
  supranational instrument) strongly matching a project activity
* requires_clarification  - weaker match, non-binding/international framework,
  or applicability depends on facts not yet supplied
* not_established / not_applicable_verified - never inferred from missing
  evidence; the latter only through human review
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RegulatoryChunk, RegulatoryDocument
from app.schemas import (
    ApplicabilityStatus,
    ContentForm,
    DocumentType,
    PotentialImpact,
    ProjectProfile,
    Provenance,
    RegulatoryRequirement,
)
from app.services.citation_service import CitationRegistry
from app.services.crosswalk import official_passage_for
from app.services.regulatory_library import is_binding
from app.services.retrieval_service import RetrievalService, RetrievedPassage, tokenize
from app.services.taxonomy import component_label, find_keywords, taxonomy

STRONG_MATCH = 0.5
_OBLIGATION_RE = re.compile(r"^Obligation:\s*(.+)$", re.M)
_PERMIT_RE = re.compile(r"^Permit:\s*(.+)$", re.M)


@dataclass
class Query:
    text: str
    activities: list[str] = field(default_factory=list)   # activity ids
    components: list[str] = field(default_factory=list)


def build_queries(profile: ProjectProfile, impacts: list[PotentialImpact]) -> list[Query]:
    tax = taxonomy()
    sector = profile.sector.value
    sector_label = tax["sectors"][sector]["label"] if sector else "development project"
    queries = [Query(f"environmental impact assessment screening requirement for {sector_label} projects"),
               Query(f"environmental permit registration requirement {sector_label}")]
    comp_by_act: dict[str, set[str]] = {}
    for imp in impacts:
        for aid in imp.activity_ids:
            comp_by_act.setdefault(aid, set()).add(imp.environmental_component)
    for act in profile.activities:
        queries.append(Query(f"{act.name} permit approval obligations {sector_label}", [act.activity_id],
                             sorted(comp_by_act.get(act.activity_id, set()))))
    high = [i for i in impacts if i.risk_priority == "high"]
    for comp in dict.fromkeys(i.environmental_component for i in high):
        acts = sorted({a for i in high if i.environmental_component == comp for a in i.activity_ids})
        queries.append(Query(f"{component_label(comp)} protection requirements assessment", acts, [comp]))
    if "near_protected_area" in profile.setting_flags:
        queries.append(Query("project affecting protected area nature conservation site assessment", [], ["biodiversity"]))
    if "near_wetland_or_waterbody" in profile.setting_flags:
        queries.append(Query("discharge to river water body status protection permit", [], ["surface_water"]))
    return queries


def _activity_overlap(text: str, profile: ProjectProfile, activity_ids: list[str]) -> bool:
    tax = taxonomy()
    acts = [a for a in profile.activities if a.activity_id in activity_ids] or profile.activities
    keywords = [kw for a in acts for kw in tax["activities"][a.key]["keywords"]]
    if profile.sector.value:
        keywords += tax["sectors"][profile.sector.value]["keywords"]
    return bool(find_keywords(text, keywords, skip_negated=False))


def map_requirements(db: Session, profile: ProjectProfile, impacts: list[PotentialImpact],
                     registry: CitationRegistry) -> tuple[list[RegulatoryRequirement], list[RetrievedPassage]]:
    retriever = RetrievalService(db)
    hits: dict[int, tuple[RetrievedPassage, set[str], set[str]]] = {}
    for q in build_queries(profile, impacts):
        for p in retriever.search(q.text, profile.jurisdiction, include_related=True, top_k=4):
            if p.chunk_id in hits:
                prev, acts, comps = hits[p.chunk_id]
                acts.update(q.activities)
                comps.update(q.components)
                if p.score > prev.score:
                    hits[p.chunk_id] = (p, acts, comps)
            else:
                hits[p.chunk_id] = (p, set(q.activities), set(q.components))

    act_names = {a.activity_id: a.name for a in profile.activities}
    requirements: list[RegulatoryRequirement] = []
    for passage, acts, comps in sorted(hits.values(), key=lambda h: -h[0].score):
        doc = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == passage.document_id))
        if doc is None:
            continue
        ref = registry.cite_passage(passage, doc)
        binding = is_binding(doc)
        uncertainty: list[str] = []
        overlap = _activity_overlap(passage.text, profile, sorted(acts))
        if passage.relationship == "reference":
            status = ApplicabilityStatus.requires_clarification
            uncertainty.append("International framework - not national law; applies only if required by financing "
                               "agreements or adopted by the competent authority.")
        elif binding and passage.score >= STRONG_MATCH and overlap:
            status = ApplicabilityStatus.potentially_applicable
        else:
            status = ApplicabilityStatus.requires_clarification
            if not overlap:
                uncertainty.append("Provision does not explicitly mention the project's activities or sector.")
            if passage.score < STRONG_MATCH:
                uncertainty.append(f"Moderate retrieval relevance ({passage.score:.2f}).")
        if passage.relationship == "supranational":
            uncertainty.append("EU-level instrument: applies through national transposing legislation; confirm the "
                               "corresponding national provision.")
        evidence_keys = [ref.citation_key]
        if doc.content_form == ContentForm.curated_summary.value:
            official = official_passage_for(db, doc.document_id, passage.section)
            official_doc = db.scalar(select(RegulatoryDocument).where(
                RegulatoryDocument.document_id == official.document_id)) if official else None
            if official is not None and official_doc is not None:
                official_ref = registry.cite_passage(official, official_doc)
                evidence_keys.append(official_ref.citation_key)
                uncertainty.append(f"Paraphrased in a curated summary; the corresponding official provision is cited "
                                   f"as [{official_ref.citation_key}] - compare the wording before relying on it.")
            else:
                uncertainty.append("Retrieved from a curated summary, not the official text; no official provision "
                                   "is linked for this passage.")
        if not doc.status_verified:
            uncertainty.append(f"Current legal status not verified ({doc.legal_status or 'unknown'}).")

        m = _OBLIGATION_RE.search(passage.text)
        obligation = m.group(1).strip() if m else None
        if obligation is None:
            related = ", ".join(act_names[a] for a in sorted(acts) if a in act_names) or "the project"
            obligation = f"Review this provision for obligations relevant to {related.lower()}."
            uncertainty.append("No explicit obligation statement could be extracted automatically.")
        permit = _PERMIT_RE.search(passage.text)
        prov = Provenance.retrieved_legislation if binding else Provenance.regulatory_guidance
        requirements.append(RegulatoryRequirement(
            requirement_id=f"REQ-{len(requirements) + 1:02d}", document_id=doc.document_id,
            regulatory_source=doc.title, issuing_authority=doc.issuing_authority, jurisdiction=doc.jurisdiction,
            jurisdiction_relationship=passage.relationship, document_type=DocumentType(doc.document_type),
            content_form=ContentForm(doc.content_form), binding=binding,
            relevant_provision=passage.section or f"chunk {passage.chunk_id}",
            related_activities=[act_names[a] for a in sorted(acts) if a in act_names],
            environmental_components=sorted(comps), potential_obligation=obligation,
            permit_or_approval=permit.group(1).strip() if permit else None,
            applicability=status, supporting_evidence=evidence_keys, uncertainty=uncertainty,
            legal_status=doc.legal_status, source_url=doc.source_url, provenance=prov))
    return requirements, [h[0] for h in hits.values()]


def find_criterion_passage(db: Session, document_id: str, section: str, title: str) -> RetrievedPassage | None:
    """Locate the stored passage that supports a screening criterion's source section."""
    doc = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == document_id))
    if doc is None or doc.status != "active":
        return None
    target = set(tokenize(f"{section} {title}"))
    best, best_score = None, 0.0
    for chunk in db.scalars(select(RegulatoryChunk).where(RegulatoryChunk.regulation_id == doc.id)):
        toks = set(tokenize(f"{chunk.section or ''} {chunk.text}"))
        sec_toks = set(tokenize(chunk.section or ""))
        score = len(target & toks) / max(1, len(target)) + 0.5 * len(target & sec_toks) / max(1, len(sec_toks))
        if score > best_score:
            best, best_score = chunk, score
    if best is None or best_score < 0.3:
        return None
    return RetrievedPassage(chunk_id=best.id, document_id=doc.document_id, title=doc.title, section=best.section,
                            page=best.page, jurisdiction=doc.jurisdiction, relationship="primary",
                            document_type=doc.document_type, content_form=doc.content_form, text=best.text,
                            score=round(min(1.0, best_score), 4), dense_score=0.0, lexical_score=round(best_score, 4))
