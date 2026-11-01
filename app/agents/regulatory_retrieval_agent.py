"""Agent 3 - Regulatory Retrieval (RAG over the jurisdiction's collection)."""

from __future__ import annotations

from app.schemas import EvidenceReference, PotentialImpact, ProjectProfile, RegulatoryRequirement
from app.tools.registry import ToolContext, ToolRegistry


def run(ctx: ToolContext, registry: ToolRegistry, profile: ProjectProfile,
        impacts: list[PotentialImpact]) -> tuple[list[RegulatoryRequirement], list[EvidenceReference]]:
    out = registry.invoke("retrieve_regulatory_provisions", {"profile": profile, "impacts": impacts}, ctx)
    return out.requirements, out.evidence  # type: ignore[attr-defined]
