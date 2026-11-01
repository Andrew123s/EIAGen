"""Evidence, citation and consistency validation (Agent 5's deterministic core).

Checks:
* every citation maps to a stored passage (citation_service.validate_citation)
* every regulatory requirement has at least one valid citation; otherwise it is
  flagged unsupported and removed from the trusted findings
* no text asserts approval, exemption, compliance or a permit decision
* findings reference existing activities / impacts (internal consistency)
* 'not applicable' statuses only come from human review
* screening criteria met on unverified criteria are flagged
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.schemas import (
    ApplicabilityStatus,
    CriterionResult,
    EvidenceReference,
    PotentialImpact,
    ProjectProfile,
    Provenance,
    RegulatoryRequirement,
    ScreeningAssessment,
    ValidationIssue,
    ValidationReport,
)
from app.services.citation_service import validate_citation

_UNSUPPORTED_PATTERNS = [
    r"\b(?:is|are|was|be|been|being)\s+(?:fully\s+)?exempt(?:ed)?\b",
    r"\bexempt(?:ed)?\s+from\s+(?:an?\s+)?(?:eia|environmental\s+impact\s+assessment|assessment|permitting)",
    r"\b(?:does|do)\s+not\s+(?:require|need)\s+(?:an?\s+)?(?:eia|environmental\s+(?:impact\s+)?assessment|"
    r"(?:environmental\s+)?(?:permit|licen[cs]e|approval))",
    r"\bno\s+(?:eia|environmental\s+(?:impact\s+)?assessment|(?:environmental\s+)?permit)\s+(?:is\s+)?(?:required|needed|necessary)",
    r"\b(?:is|are)\s+(?:fully\s+)?(?:legally\s+)?compliant\b",
    r"\bfully\s+compliant\b",
    r"\bcomplies\s+with\s+(?:all\s+)?(?:the\s+)?(?:applicable\s+)?(?:law|laws|legislation|regulations|requirements)",
    r"\bmeets\s+all\s+(?:legal|regulatory|statutory)\s+requirements",
    r"\b(?:project|permit|application|eia|report)\s+(?:is|has\s+been)\s+(?:hereby\s+)?(?:approved|granted|authori[sz]ed)",
    r"\bpermit\s+(?:is|has\s+been)\s+(?:granted|issued)",
    r"\bapproved\s+eia\b",
]
_UNSUPPORTED_RE = re.compile("|".join(_UNSUPPORTED_PATTERNS), re.IGNORECASE)
# A match in a negated or conditional clause is not an assertion: "NOT AN APPROVED EIA",
# "unless ... an environmental permit has been issued", "if the permit is granted".
_GUARD_RE = re.compile(r"\b(?:not|no|unless|until|before|if|whether|without|never|once|after)\b", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"[.;:|]\s|\n")


def _asserted(sentence: str) -> bool:
    for m in _UNSUPPORTED_RE.finditer(sentence):
        prefix = sentence[max(0, m.start() - 120):m.start()]
        breaks = list(_CLAUSE_BREAK.finditer(prefix))
        if breaks:
            prefix = prefix[breaks[-1].end():]
        if not _GUARD_RE.search(prefix):
            return True
    return False


def find_unsupported_claims(text: str) -> list[str]:
    """Sentences asserting a legal decision the system is not allowed to make."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [s.strip() for s in sentences if s.strip() and _asserted(s)]


def validate_assessment(db: Session, project_id: int, profile: ProjectProfile, impacts: list[PotentialImpact],
                        requirements: list[RegulatoryRequirement], evidence: list[EvidenceReference],
                        assessment: ScreeningAssessment, narratives: list[str] | None = None,
                        ) -> tuple[ValidationReport, list[RegulatoryRequirement]]:
    """Validate findings; returns the report and the requirements that remain trusted."""
    issues: list[ValidationIssue] = []
    unsupported: list[str] = []

    citation_results = [validate_citation(db, ref, profile.jurisdiction, project_id) for ref in evidence]
    by_key = {r.citation_key: r for r in citation_results}
    for ref, res in zip(evidence, citation_results):
        ref.verified = res.valid
        ref.validation_issues = res.issues
        if not res.valid:
            issues.append(ValidationIssue(severity="error", code="citation_invalid", finding_ref=ref.citation_key,
                                          message=f"Citation {ref.citation_key} failed verification: {'; '.join(res.issues)}"))

    trusted: list[RegulatoryRequirement] = []
    for req in requirements:
        valid = [k for k in req.supporting_evidence if k in by_key and by_key[k].valid]
        if not valid:
            unsupported.append(f"{req.requirement_id} ({req.relevant_provision}) - no verifiable supporting passage")
            issues.append(ValidationIssue(severity="error", code="requirement_unsupported", finding_ref=req.requirement_id,
                                          message="Requirement has no verifiable citation and was excluded from trusted findings"))
            continue
        if req.applicability == ApplicabilityStatus.not_applicable_verified and req.provenance != Provenance.human_review:
            issues.append(ValidationIssue(severity="error", code="unverified_not_applicable", finding_ref=req.requirement_id,
                                          message="'Not applicable' may only be set through human review; reset to requires clarification"))
            req.applicability = ApplicabilityStatus.requires_clarification
        if req.content_form.value == "curated_summary":
            issues.append(ValidationIssue(severity="warning", code="curated_source", finding_ref=req.requirement_id,
                                          message="Supported only by a curated summary; verify against the official text"))
        for claim in find_unsupported_claims(req.potential_obligation):
            unsupported.append(f"{req.requirement_id}: {claim}")
            issues.append(ValidationIssue(severity="error", code="unsupported_legal_conclusion",
                                          finding_ref=req.requirement_id, message=f"Unsupported legal conclusion: {claim}"))
        trusted.append(req)

    activity_ids = {a.activity_id for a in profile.activities}
    for imp in impacts:
        if not imp.beneficial and not imp.activity_ids:
            issues.append(ValidationIssue(severity="error", code="impact_without_activity", finding_ref=imp.impact_id,
                                          message="Potential impact is not linked to any project activity"))
        unknown = set(imp.activity_ids) - activity_ids
        if unknown:
            issues.append(ValidationIssue(severity="error", code="impact_unknown_activity", finding_ref=imp.impact_id,
                                          message=f"References unknown activities: {sorted(unknown)}"))
        if imp.provenance == Provenance.ai_interpretation:
            issues.append(ValidationIssue(severity="info", code="ai_interpretation", finding_ref=imp.impact_id,
                                          message="Identified from AI-inferred activities only; low confidence"))

    for crit in assessment.regulatory_screening.criteria:
        if crit.result == CriterionResult.met and crit.verification_status != "verified":
            issues.append(ValidationIssue(severity="warning", code="criterion_unverified", finding_ref=crit.criterion_id,
                                          message="Criterion met, but the criterion itself has not been verified against the official text"))
        if crit.result == CriterionResult.met and not crit.evidence:
            issues.append(ValidationIssue(severity="warning", code="criterion_without_passage", finding_ref=crit.criterion_id,
                                          message="No stored passage found for the criterion's source section"))
        for key in crit.evidence:
            if key in by_key and not by_key[key].valid:
                issues.append(ValidationIssue(severity="error", code="criterion_citation_invalid", finding_ref=crit.criterion_id,
                                              message=f"Criterion citation {key} failed verification"))

    texts = list(narratives or []) + assessment.rationale + [assessment.regulatory_screening.summary]
    for text in texts:
        for claim in find_unsupported_claims(text):
            unsupported.append(claim)
            issues.append(ValidationIssue(severity="error", code="unsupported_legal_conclusion",
                                          message=f"Unsupported legal conclusion detected: {claim}"))

    for warning in profile.injection_warnings:
        issues.append(ValidationIssue(severity="warning", code="prompt_injection_detected",
                                      message=f"Instruction-like text in untrusted input was ignored: {warning}"))

    blocking = [i for i in issues if i.severity == "error" and i.code not in ("requirement_unsupported", "citation_invalid")]
    valid_count = sum(1 for r in citation_results if r.valid)
    return ValidationReport(passed=not blocking, issues=issues, citation_results=citation_results,
                            unsupported_claims=unsupported, citations_checked=len(citation_results),
                            citations_valid=valid_count), trusted
