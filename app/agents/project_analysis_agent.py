"""Agent 1 - Project Analysis.

Runs the deterministic extraction tool, then (if an LLM is configured) asks the
LLM for *supplementary* interpretation. LLM suggestions are accepted only if
they validate against ``LLMProfileSupplement``, use known taxonomy keys, and -
for numeric parameters - quote text that appears verbatim in the input. They
are tagged ``ai_interpretation`` with low confidence; rejected suggestions are
recorded in ``ai_notes``.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.security import neutralise_injection, wrap_untrusted
from app.llm.providers import LLMError, LLMProvider
from app.schemas import (
    Confidence,
    InfoStatus,
    ProjectActivity,
    ProjectParameter,
    ProjectPhase,
    ProjectProfile,
    Provenance,
)
from app.services.analysis_service import refresh_derived
from app.services.project_parser import ProjectInput
from app.services.taxonomy import rules, taxonomy
from app.services.validation_service import find_unsupported_claims
from app.tools.registry import ToolContext, ToolRegistry


class LLMParameter(BaseModel):
    name: str
    value: float
    quote: str = Field(description="Exact text from the project description stating this value")


class LLMProfileSupplement(BaseModel):
    additional_activities: list[str] = Field(default_factory=list, description="Activity keys from the taxonomy")
    parameters: list[LLMParameter] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, max_length=5)


def _prompt(inp: ProjectInput, profile: ProjectProfile) -> tuple[str, str]:
    tax = taxonomy()
    act_list = "\n".join(f"- {k}: {v['label']}" for k, v in tax["activities"].items())
    params = ", ".join(rules()["parameters"])
    system = ("Identify project activities that a deterministic keyword parser may have missed. Use only activity "
              "keys from the provided list. Report numeric parameters only if the exact value is stated in the "
              "text, quoting the exact words. Notes must describe uncertainties, not conclusions.")
    found = ", ".join(a.key for a in profile.activities) or "none"
    text = "\n\n".join(s.text for s in inp.sources())
    user = (f"Allowed activity keys:\n{act_list}\n\nAllowed parameter names: {params}\n\n"
            f"Activities already identified: {found}\n\n{wrap_untrusted('project_information', text)}")
    return system, user


def run(ctx: ToolContext, registry: ToolRegistry, llm: LLMProvider) -> ProjectProfile:
    profile = registry.invoke("extract_project_profile", {"project_id": ctx.project_id or 0}, ctx)
    assert isinstance(profile, ProjectProfile)
    if not llm.available:
        return profile

    inp: ProjectInput = ctx.state["project_input"]
    system, user = _prompt(inp, profile)
    try:
        supplement = llm.structured(system, user, LLMProfileSupplement)
    except LLMError as exc:
        profile.ai_notes.append(f"LLM unavailable ({exc}); deterministic extraction only.")
        return profile
    if supplement is None:
        profile.ai_notes.append("LLM returned no valid structured output; deterministic extraction only.")
        return profile

    tax = taxonomy()
    defs = rules()["parameters"]
    source_text = " ".join(s.text for s in inp.sources())
    norm_source = re.sub(r"\s+", " ", source_text).lower()
    existing = profile.activity_keys()
    for key in supplement.additional_activities:
        if key not in tax["activities"]:
            profile.ai_notes.append(f"Rejected AI suggestion: unknown activity key '{key[:60]}'")
            continue
        if key in existing:
            continue
        spec = tax["activities"][key]
        profile.activities.append(ProjectActivity(
            activity_id="", key=key, name=spec["label"], phases=[ProjectPhase(p) for p in spec["phases"]],
            matched_text=["AI interpretation of the project description (not stated explicitly)"],
            status=InfoStatus.inferred, provenance=Provenance.ai_interpretation, confidence=Confidence.low))
        existing.add(key)
    have = {p.name for p in profile.parameters}
    for p in supplement.parameters:
        quote = re.sub(r"\s+", " ", p.quote).strip().lower()
        if p.name not in defs or p.name in have:
            continue
        number_in_quote = re.search(re.escape(f"{p.value:g}"), quote.replace(",", ""))
        if not quote or quote not in norm_source or not number_in_quote:
            profile.ai_notes.append(f"Rejected AI parameter '{p.name}': quoted text not found verbatim in the input")
            continue
        profile.parameters.append(ProjectParameter(
            name=p.name, label=defs[p.name]["label"], value=p.value, unit=defs[p.name]["unit"], source_text=p.quote,
            status=InfoStatus.extracted, provenance=Provenance.ai_interpretation))
    for note in supplement.notes:
        clean, flagged = neutralise_injection(note)
        if flagged or find_unsupported_claims(clean):
            profile.ai_notes.append("Rejected AI note containing an instruction or legal conclusion")
            continue
        profile.ai_notes.append(f"AI note: {clean[:300]}")
    order = list(tax["activities"])
    profile.activities.sort(key=lambda a: order.index(a.key))
    profile.parameters.sort(key=lambda x: x.name)
    return refresh_derived(profile, inp)
