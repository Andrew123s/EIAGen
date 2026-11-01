"""Agent 2 - Environmental Impact.

Identifies potential impacts through the impact tool and links impacts derived
from uploaded documents to the supporting document page ([DOC-xx] citations).
"""

from __future__ import annotations

from app.models import Project
from app.schemas import PotentialImpact, ProjectProfile, Provenance
from app.services.citation_service import CitationRegistry
from app.tools.registry import ToolContext, ToolRegistry


def _document_citations(ctx: ToolContext, profile: ProjectProfile) -> dict[str, str]:
    """activity_id -> DOC citation key for activities found in uploaded documents."""
    project: Project | None = ctx.state.get("project")
    registry: CitationRegistry = ctx.state.setdefault("citations", CitationRegistry())
    out: dict[str, str] = {}
    if project is None:
        return out
    for act in profile.activities:
        if act.provenance != Provenance.uploaded_document or not act.matched_text:
            continue
        snippet = act.matched_text[0]
        for doc in project.documents:
            for page in doc.pages or []:
                if snippet in page.get("text", "") or snippet in " ".join(page.get("text", "").split()):
                    ref = registry.cite_project_document(doc, page.get("page"), snippet)
                    out[act.activity_id] = ref.citation_key
                    break
            if act.activity_id in out:
                break
    return out


def run(ctx: ToolContext, registry: ToolRegistry, profile: ProjectProfile) -> list[PotentialImpact]:
    result = registry.invoke("identify_environmental_impacts", profile, ctx)
    impacts: list[PotentialImpact] = result.impacts  # type: ignore[attr-defined]
    doc_refs = _document_citations(ctx, profile)
    for imp in impacts:
        for aid in imp.activity_ids:
            if aid in doc_refs and f"[{doc_refs[aid]}]" not in imp.evidence:
                imp.evidence.append(f"[{doc_refs[aid]}] activity stated in uploaded project document")
    return impacts
