"""Agent 6 - Report Generation.

Drafts the executive summary. The deterministic template is always produced;
if an LLM is configured, it may rephrase the summary from the *validated
structured findings only* (no raw documents). An LLM draft is discarded if it
contains an unsupported legal conclusion or cites a key that does not exist.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.llm.providers import LLMError, LLMProvider
from app.schemas import (
    OUTCOME_LABELS,
    EvidenceReference,
    PotentialImpact,
    ProjectProfile,
    RegulatoryRequirement,
    ScreeningAssessment,
)
from app.services.validation_service import find_unsupported_claims


class ExecutiveSummaryDraft(BaseModel):
    summary: str = Field(min_length=50, max_length=4000)


def deterministic_summary(profile: ProjectProfile, impacts: list[PotentialImpact],
                          requirements: list[RegulatoryRequirement], assessment: ScreeningAssessment) -> str:
    risk = assessment.environmental_risk_screening.counts
    sector = (profile.sector.value or "unspecified sector").replace("_", " ")
    critical = [m for m in profile.missing_information if m.importance == "critical"]
    potentially = [r for r in requirements if r.applicability.value == "potentially_applicable"]
    parts = [
        f"This preliminary screening concerns '{profile.project_name}' ({sector}) under the {profile.jurisdiction} "
        f"regulatory knowledge base. {len(profile.activities)} project activities were identified from the "
        f"information supplied, giving rise to {len([i for i in impacts if not i.beneficial])} potential adverse "
        f"impacts for investigation ({risk['high']} high, {risk['medium']} medium and {risk['low']} low priority "
        f"under the EIAGen-RS-1 heuristic) and {risk['beneficial']} potential beneficial "
        f"effect{'' if risk['beneficial'] == 1 else 's'}.",
        f"{len(requirements)} regulatory provisions were retrieved, of which {len(potentially)} are flagged as "
        f"potentially applicable and the remainder require clarification. "
        f"{assessment.regulatory_screening.summary}",
        "Preliminary screening outcome: " + "; ".join(OUTCOME_LABELS[o] for o in assessment.outcomes) + ".",
    ]
    if critical:
        parts.append(f"{len(critical)} critical information gaps limit the screening, including: "
                     + "; ".join(m.description.lower() for m in critical[:4]) + ".")
    parts.append("All findings are potential and preliminary and must be verified by qualified practitioners and, "
                 "where applicable, the competent authority.")
    return " ".join(parts)


def draft_summary(llm: LLMProvider, profile: ProjectProfile, impacts: list[PotentialImpact],
                  requirements: list[RegulatoryRequirement], assessment: ScreeningAssessment,
                  evidence: list[EvidenceReference]) -> dict:
    base = deterministic_summary(profile, impacts, requirements, assessment)
    result = {"executive_summary": base, "source": "deterministic_template", "llm_draft_rejected": None}
    if not llm.available:
        return result
    findings = {
        "project": profile.project_name, "sector": profile.sector.value, "jurisdiction": profile.jurisdiction,
        "outcomes": [OUTCOME_LABELS[o] for o in assessment.outcomes],
        "regulatory_screening": assessment.regulatory_screening.summary,
        "top_risks": assessment.environmental_risk_screening.top_risks,
        "requirements": [{"id": r.requirement_id, "source": r.regulatory_source, "provision": r.relevant_provision,
                          "applicability": r.applicability.value, "citations": r.supporting_evidence}
                         for r in requirements[:12]],
        "critical_gaps": [m.description for m in profile.missing_information if m.importance == "critical"],
    }
    system = ("Write a concise executive summary (150-250 words) of a preliminary EIA screening from the structured "
              "findings. Use cautious language ('potential', 'may', 'requires verification'). Cite only the citation "
              "keys provided. Do not state or imply approval, exemption, compliance or permit decisions.")
    try:
        draft = llm.structured(system, json.dumps(findings), ExecutiveSummaryDraft)
    except LLMError as exc:
        result["llm_draft_rejected"] = f"LLM error: {exc}"
        return result
    if draft is None:
        return result
    valid_keys = {e.citation_key for e in evidence}
    cited = set(re.findall(r"\[?((?:REG|GUIDE|DOC|INTL)-\d{2,})\]?", draft.summary))
    claims = find_unsupported_claims(draft.summary)
    if claims or (cited - valid_keys):
        reason = (f"unsupported legal conclusion: {claims[0]}" if claims
                  else f"unknown citation keys {sorted(cited - valid_keys)}")
        result["llm_draft_rejected"] = reason
        return result
    result.update({"executive_summary": draft.summary, "source": "ai_interpretation"})
    return result
