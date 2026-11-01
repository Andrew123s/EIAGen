"""Regulatory search and provision-retrieval tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas import EvidenceReference, PotentialImpact, ProjectProfile, RegulatoryRequirement, RegulationSearchHit
from app.services.citation_service import CitationRegistry
from app.services.regulatory_mapper import map_requirements
from app.services.retrieval_service import RetrievalService
from app.tools.registry import Tool, ToolContext


class SearchInput(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    jurisdiction: str
    include_related: bool = True
    top_k: int = Field(default=6, ge=1, le=30)


class SearchOutput(BaseModel):
    hits: list[RegulationSearchHit]


class ProvisionInput(BaseModel):
    profile: ProjectProfile
    impacts: list[PotentialImpact]


class ProvisionOutput(BaseModel):
    requirements: list[RegulatoryRequirement]
    evidence: list[EvidenceReference]


def search_regulatory_documents(data: SearchInput, ctx: ToolContext) -> SearchOutput:
    hits = RetrievalService(ctx.db).search(data.query, data.jurisdiction, data.include_related, data.top_k)
    return SearchOutput(hits=[RegulationSearchHit(
        chunk_id=h.chunk_id, document_id=h.document_id, title=h.title, section=h.section, page=h.page,
        jurisdiction=h.jurisdiction, relationship=h.relationship, document_type=h.document_type,
        content_form=h.content_form, score=h.score, text=h.text) for h in hits])


def retrieve_regulatory_provisions(data: ProvisionInput, ctx: ToolContext) -> ProvisionOutput:
    registry: CitationRegistry = ctx.state.setdefault("citations", CitationRegistry())
    requirements, _ = map_requirements(ctx.db, data.profile, data.impacts, registry)
    return ProvisionOutput(requirements=requirements, evidence=list(registry.items))


TOOLS = [
    Tool("search_regulatory_documents", "Semantic + lexical search of the selected jurisdiction's regulatory "
         "collection (with labelled related collections). Returns passages with source metadata.",
         SearchInput, SearchOutput, search_regulatory_documents,
         ("regulatory_requirement_retrieval", "library"), llm_callable=True),
    Tool("retrieve_regulatory_provisions", "Retrieve provisions relevant to the project's activities and impacts and "
         "assess their applicability status with uncertainty and citations.",
         ProvisionInput, ProvisionOutput, retrieve_regulatory_provisions, ("regulatory_requirement_retrieval",)),
]
