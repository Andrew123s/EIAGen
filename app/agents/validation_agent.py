"""Agent 5 - Validation: citations, unsupported conclusions, consistency."""

from __future__ import annotations

from app.schemas import (
    EvidenceReference,
    PotentialImpact,
    ProjectProfile,
    RegulatoryRequirement,
    ScreeningAssessment,
    ValidationReport,
)
from app.services.validation_service import validate_assessment
from app.tools.registry import ToolContext, ToolRegistry


def run(ctx: ToolContext, registry: ToolRegistry, profile: ProjectProfile, impacts: list[PotentialImpact],
        requirements: list[RegulatoryRequirement], evidence: list[EvidenceReference],
        assessment: ScreeningAssessment, narratives: list[str] | None = None,
        ) -> tuple[ValidationReport, list[RegulatoryRequirement]]:
    # Logged tool call (explicit citation verification), then the full rule set.
    registry.invoke("validate_citations", {"jurisdiction": profile.jurisdiction, "evidence": evidence}, ctx)
    return validate_assessment(ctx.db, ctx.project_id or 0, profile, impacts, requirements, evidence,
                               assessment, narratives)
