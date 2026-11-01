"""Agent 4 - Screening.

Combines the profile, potential impacts and the jurisdiction's encoded criteria
into a preliminary screening assessment (deterministic tool), attaches the
stored source passage of each criterion, and prepares mitigation
considerations and further investigations.
"""

from __future__ import annotations

from app.schemas import (
    FurtherInvestigation,
    MitigationMeasure,
    PotentialImpact,
    ProjectProfile,
    RegulatoryRequirement,
    ScreeningAssessment,
)
from app.services.citation_service import CitationRegistry
from app.services.crosswalk import resolve_anchor
from app.services.taxonomy import rules
from app.services.mitigation_service import generate_mitigation, identify_investigations
from app.services.regulatory_mapper import find_criterion_passage
from app.services.regulatory_library import get_document, LibraryError
from app.tools.registry import ToolContext, ToolRegistry


def run(ctx: ToolContext, registry: ToolRegistry, profile: ProjectProfile, impacts: list[PotentialImpact],
        requirements: list[RegulatoryRequirement], project_text: str,
        ) -> tuple[ScreeningAssessment, list[MitigationMeasure], list[FurtherInvestigation]]:
    assessment = registry.invoke("evaluate_screening_criteria", {
        "profile": profile, "impacts": impacts, "missing_information": profile.missing_information,
        "project_text": project_text}, ctx)
    assert isinstance(assessment, ScreeningAssessment)

    citations: CitationRegistry = ctx.state.setdefault("citations", CitationRegistry())
    specs = {c["id"]: c for c in rules()["regulatory_screening_criteria"]}
    for crit in assessment.regulatory_screening.criteria:
        keys: list[str] = []
        passage = find_criterion_passage(ctx.db, crit.source_document_id, crit.source_section, crit.title)
        if passage is not None:
            try:
                keys.append(citations.cite_passage(passage, get_document(ctx.db, crit.source_document_id)).citation_key)
            except LibraryError:
                pass
        official = (specs.get(crit.criterion_id) or {}).get("official_source")
        if official:
            op = resolve_anchor(ctx.db, official["document_id"], official["anchor"])
            if op is not None:
                keys.append(citations.cite_passage(op, get_document(ctx.db, op.document_id)).citation_key)
        crit.evidence = keys

    binding_reqs = [r for r in requirements if r.binding and r.applicability.value == "potentially_applicable"]
    mitigation = generate_mitigation(impacts, profile, binding_reqs)
    investigations = identify_investigations(impacts)
    return assessment, mitigation, investigations
