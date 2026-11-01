"""Citation verification tool."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas import CitationValidationResult, EvidenceReference
from app.services.citation_service import validate_citation
from app.tools.registry import Tool, ToolContext


class CitationInput(BaseModel):
    jurisdiction: str
    evidence: list[EvidenceReference]


class CitationOutput(BaseModel):
    results: list[CitationValidationResult]
    valid: int
    invalid: int


def validate_citations(data: CitationInput, ctx: ToolContext) -> CitationOutput:
    results = [validate_citation(ctx.db, ref, data.jurisdiction, ctx.project_id) for ref in data.evidence]
    valid = sum(r.valid for r in results)
    return CitationOutput(results=results, valid=valid, invalid=len(results) - valid)


TOOLS = [
    Tool("validate_citations", "Verify that each citation maps to a stored passage: document, chunk, page, section, "
         "verbatim passage, jurisdiction, version and status.",
         CitationInput, CitationOutput, validate_citations, ("evidence_citation_validation", "library"),
         llm_callable=True),
]
