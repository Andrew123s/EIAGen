"""Pydantic schemas for all major EIAGen outputs and API payloads.

Every workflow stage produces one of these models and is validated before the
next stage runs. LLM output is only accepted after it validates against the
corresponding schema, and it is always tagged with AI-interpretation provenance.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISCLAIMER = (
    "This report is an AI-assisted preliminary environmental screening assessment. It is based on "
    "the project information and regulatory documents available to the system at the time of "
    "preparation. It does not constitute a legally validated EIA determination, environmental "
    "permit, regulatory approval, or professional certification. All regulatory interpretations, "
    "project classifications, impact assessments, and recommendations require review and "
    "verification by appropriately qualified environmental practitioners and, where applicable, "
    "the competent regulatory authority."
)


# ---------------------------------------------------------------- Enums


class Provenance(str, Enum):
    user_input = "user_input"
    uploaded_document = "uploaded_document"
    retrieved_legislation = "retrieved_legislation"
    regulatory_guidance = "regulatory_guidance"
    ai_interpretation = "ai_interpretation"
    deterministic_rule = "deterministic_rule"
    human_review = "human_review"


class InfoStatus(str, Enum):
    user_provided = "user_provided"
    extracted = "extracted"
    inferred = "inferred"
    missing = "missing"


class ProjectPhase(str, Enum):
    pre_construction = "pre_construction"
    construction = "construction"
    operation = "operation"
    decommissioning = "decommissioning"


class Likelihood(str, Enum):
    unlikely = "unlikely"
    possible = "possible"
    likely = "likely"


class Severity(str, Enum):
    minor = "minor"
    moderate = "moderate"
    major = "major"


class Duration(str, Enum):
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"
    permanent = "permanent"


class Extent(str, Enum):
    site = "site"
    local = "local"
    regional = "regional"
    transboundary = "transboundary"


class Reversibility(str, Enum):
    reversible = "reversible"
    partially_reversible = "partially_reversible"
    irreversible = "irreversible"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Sensitivity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ApplicabilityStatus(str, Enum):
    potentially_applicable = "potentially_applicable"
    requires_clarification = "requires_clarification"
    not_established = "not_established"
    not_applicable_verified = "not_applicable_verified"


class DocumentType(str, Enum):
    legislation = "legislation"
    regulation = "regulation"
    guideline = "guideline"
    technical_standard = "technical_standard"
    international_framework = "international_framework"
    non_binding_recommendation = "non_binding_recommendation"


BINDING_TYPES = {DocumentType.legislation, DocumentType.regulation}


class ContentForm(str, Enum):
    official_text = "official_text"
    curated_summary = "curated_summary"


class DocumentStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    withdrawn = "withdrawn"


class CriterionResult(str, Enum):
    met = "met"
    not_met = "not_met"
    indeterminate = "indeterminate"


class ScreeningOutcome(str, Enum):
    further_assessment_potentially_warranted = "further_assessment_potentially_warranted"
    additional_information_required = "additional_information_required"
    regulatory_category_identified = "regulatory_category_identified"
    no_determination_possible = "no_determination_possible"
    human_review_required = "human_review_required"


OUTCOME_LABELS = {
    ScreeningOutcome.further_assessment_potentially_warranted: "Further assessment potentially warranted",
    ScreeningOutcome.additional_information_required: "Additional project information required",
    ScreeningOutcome.regulatory_category_identified: "Potentially relevant regulatory screening category identified",
    ScreeningOutcome.no_determination_possible: "No determination possible from available information",
    ScreeningOutcome.human_review_required: "Human regulatory review required",
}


class ReviewState(str, Enum):
    draft = "draft"
    ai_analysis_completed = "ai_analysis_completed"
    awaiting_human_review = "awaiting_human_review"
    changes_requested = "changes_requested"
    human_reviewed = "human_reviewed"
    final_preliminary_report_issued = "final_preliminary_report_issued"


class MitigationLevel(str, Enum):
    avoid = "avoid"
    minimize = "minimize"
    restore = "restore"
    offset = "offset"


class MeasureType(str, Enum):
    general_good_practice = "general_good_practice"
    project_specific = "project_specific"
    regulatory_requirement = "regulatory_requirement"
    specialist_design_required = "specialist_design_required"


class VerificationStatus(str, Enum):
    unverified = "unverified"
    verified = "verified"
    rejected = "rejected"
    corrected = "corrected"


# ------------------------------------------------------- Project intake


class Coordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ProjectCreate(BaseModel):
    """Structured project registration (sections A-D of the intake form)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    developer: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=20, max_length=20000)
    sector: str | None = Field(default=None, max_length=60)
    phase: str | None = Field(default=None, max_length=60)
    objectives: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=300)
    jurisdiction: str = Field(min_length=2, max_length=40)
    region: str | None = Field(default=None, max_length=120)
    coordinates: Coordinates | None = None
    characteristics: dict[str, str | float | int] = Field(default_factory=dict)
    environmental_setting: str | None = Field(default=None, max_length=8000)

    @field_validator("characteristics")
    @classmethod
    def _limit_characteristics(cls, v: dict) -> dict:
        if len(v) > 60:
            raise ValueError("Too many characteristics (max 60)")
        for key in v:
            if len(key) > 60:
                raise ValueError("Characteristic names must be at most 60 characters")
        return v


class ProjectSummary(BaseModel):
    id: int
    name: str
    developer: str | None
    sector: str | None
    jurisdiction: str
    region: str | None
    review_state: ReviewState
    created_at: datetime
    latest_screening_version: int | None = None


class ProjectDetail(ProjectSummary):
    description: str
    phase: str | None
    objectives: str | None
    location: str | None
    coordinates: Coordinates | None
    characteristics: dict[str, Any]
    environmental_setting: str | None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    profile: dict[str, Any] | None = None


# ------------------------------------------------------- Project profile


class ProjectParameter(BaseModel):
    name: str
    label: str
    value: float
    unit: str
    source_text: str | None = None
    status: InfoStatus
    provenance: Provenance


class ProjectActivity(BaseModel):
    activity_id: str
    key: str
    name: str
    phases: list[ProjectPhase]
    matched_text: list[str] = Field(default_factory=list)
    status: InfoStatus
    provenance: Provenance
    confidence: Confidence


class EnvironmentalPressure(BaseModel):
    key: str
    name: str
    source_activities: list[str]


class EnvironmentalReceptor(BaseModel):
    key: str
    name: str
    component: str
    sensitivity: Sensitivity
    presence_confirmed: bool
    basis: str
    provenance: Provenance


class MissingInformation(BaseModel):
    key: str
    description: str
    importance: Literal["critical", "important", "useful"]
    clarification_question: str
    provenance: Provenance = Provenance.deterministic_rule


class ProfileField(BaseModel):
    value: Any = None
    status: InfoStatus
    provenance: Provenance | None = None


class ProjectProfile(BaseModel):
    """Structured interpretation of a project. Preliminary, never verified fact."""

    project_name: str
    jurisdiction: str
    sector: ProfileField
    location: ProfileField
    region: ProfileField
    coordinates: ProfileField
    phase: ProfileField
    environmental_setting: ProfileField
    activities: list[ProjectActivity]
    phases: list[ProjectPhase]
    parameters: list[ProjectParameter]
    pressures: list[EnvironmentalPressure]
    receptors: list[EnvironmentalReceptor]
    setting_flags: list[str] = Field(default_factory=list)
    missing_information: list[MissingInformation]
    clarification_questions: list[str]
    injection_warnings: list[str] = Field(default_factory=list)
    geospatial_notes: list[str] = Field(default_factory=list)
    ai_notes: list[str] = Field(default_factory=list)
    interpretation_note: str = (
        "Preliminary interpretation of the supplied project description. Activities, pressures "
        "and receptors are not verified findings."
    )

    @model_validator(mode="after")
    def _activities_have_ids(self) -> "ProjectProfile":
        ids = [a.activity_id for a in self.activities]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate activity identifiers in project profile")
        return self

    def parameter(self, name: str) -> float | None:
        for p in self.parameters:
            if p.name == name:
                return p.value
        return None

    def activity_keys(self) -> set[str]:
        return {a.key for a in self.activities}


class ProfileCorrection(BaseModel):
    """User corrections applied on the Project Analysis page before screening."""

    model_config = ConfigDict(extra="forbid")

    sector: str | None = None
    location: str | None = None
    region: str | None = None
    phase: str | None = None
    environmental_setting: str | None = None
    add_activities: list[str] = Field(default_factory=list)
    remove_activities: list[str] = Field(default_factory=list)
    parameters: dict[str, float] = Field(default_factory=dict)
    corrected_by: str = Field(min_length=2, max_length=120)


# ------------------------------------------------------------- Evidence


class EvidenceReference(BaseModel):
    citation_key: str = Field(pattern=r"^(REG|GUIDE|DOC|INTL)-\d{2,}$")
    source_type: Literal["regulatory", "project_document"]
    document_id: str
    chunk_id: int | None = None
    title: str
    section: str | None = None
    page: int | None = None
    passage: str
    jurisdiction: str | None = None
    version: str | None = None
    source_url: str | None = None
    content_form: ContentForm | None = None
    document_type: DocumentType | None = None
    relevance_score: float | None = None
    verified: bool = False
    validation_issues: list[str] = Field(default_factory=list)

    def formatted(self) -> str:
        parts = [f"[{self.citation_key}] {self.title}"]
        if self.section:
            parts.append(self.section)
        if self.page:
            parts.append(f"page {self.page}")
        return ", ".join(parts) + "."


# --------------------------------------------------------------- Impacts


class PotentialImpact(BaseModel):
    impact_id: str
    impact_key: str
    project_activity: str
    activity_ids: list[str]
    environmental_component: str
    component_category: str
    potential_impact: str
    potential_receptor: str
    receptor_keys: list[str]
    impact_pathway: str
    project_phases: list[ProjectPhase]
    geographical_extent: Extent
    duration: Duration
    reversibility: Reversibility
    likelihood: Likelihood
    severity: Severity
    receptor_sensitivity: Sensitivity
    confidence: Confidence
    beneficial: bool = False
    risk_score: int | None = Field(default=None, ge=1, le=9)
    risk_priority: Literal["low", "medium", "high"] | None = None
    scoring_notes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    supporting_rationale: str
    additional_information_required: list[str] = Field(default_factory=list)
    status: Literal["potential"] = "potential"
    provenance: Provenance = Provenance.deterministic_rule
    verification_status: VerificationStatus = VerificationStatus.unverified


# --------------------------------------------------- Regulatory mapping


class RegulatoryRequirement(BaseModel):
    requirement_id: str
    document_id: str
    regulatory_source: str
    issuing_authority: str
    jurisdiction: str
    jurisdiction_relationship: Literal["primary", "supranational", "reference"]
    document_type: DocumentType
    content_form: ContentForm
    binding: bool
    relevant_provision: str
    related_activities: list[str]
    environmental_components: list[str]
    potential_obligation: str
    permit_or_approval: str | None = None
    applicability: ApplicabilityStatus
    supporting_evidence: list[str] = Field(min_length=1)
    uncertainty: list[str] = Field(default_factory=list)
    verification_required: Literal[True] = True
    legal_status: str | None = None
    source_url: str | None = None
    provenance: Provenance
    verification_status: VerificationStatus = VerificationStatus.unverified


# ------------------------------------------------------------ Screening


class ScreeningCriterion(BaseModel):
    criterion_id: str
    jurisdiction: str
    title: str
    description: str
    source_document_id: str
    source_section: str
    screening_category: str
    verification_status: Literal["verified", "unverified"]
    result: CriterionResult
    outcome_if_met: str
    explanation: str
    parameters_used: dict[str, float | None] = Field(default_factory=dict)
    missing_parameters: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    official_text_check: dict[str, str] | None = None
    provenance: Provenance = Provenance.deterministic_rule


class RegulatoryScreening(BaseModel):
    jurisdiction: str
    determinable: bool
    criteria: list[ScreeningCriterion]
    categories_identified: list[str]
    summary: str
    notes: list[str] = Field(default_factory=list)


class RiskScreening(BaseModel):
    methodology_version: str
    methodology_description: str
    counts: dict[str, int]
    top_risks: list[str]
    component_summary: dict[str, str]
    assumptions: list[str]
    limitations: list[str]


class MitigationMeasure(BaseModel):
    measure_id: str
    impact_id: str
    hierarchy_level: MitigationLevel
    measure: str
    measure_type: MeasureType
    rationale: str
    provenance: Provenance
    note: str = "Preliminary consideration; not evidence that the impact is controlled."
    verification_status: VerificationStatus = VerificationStatus.unverified


class FurtherInvestigation(BaseModel):
    investigation_id: str
    name: str
    reason: str
    related_impacts: list[str]
    priority: Literal["low", "medium", "high"]


class ScreeningAssessment(BaseModel):
    regulatory_screening: RegulatoryScreening
    environmental_risk_screening: RiskScreening
    outcomes: list[ScreeningOutcome] = Field(min_length=1)
    outcome_labels: list[str]
    rationale: list[str]
    human_review_required: Literal[True] = True
    preliminary: Literal[True] = True
    limitations: list[str]

    @model_validator(mode="after")
    def _always_human_review(self) -> "ScreeningAssessment":
        if ScreeningOutcome.human_review_required not in self.outcomes:
            raise ValueError("Screening outcomes must always include human_review_required")
        return self


# ----------------------------------------------------------- Validation


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    finding_ref: str | None = None


class CitationValidationResult(BaseModel):
    citation_key: str
    valid: bool
    checks: dict[str, bool]
    issues: list[str]


class ValidationReport(BaseModel):
    passed: bool
    issues: list[ValidationIssue]
    citation_results: list[CitationValidationResult]
    unsupported_claims: list[str]
    citations_checked: int
    citations_valid: int


# --------------------------------------------------------------- Report


class ReportSection(BaseModel):
    number: int
    title: str
    paragraphs: list[str] = Field(default_factory=list)
    table: dict[str, Any] | None = None   # {"columns": [...], "rows": [[...], ...]}


class ScreeningReport(BaseModel):
    report_title: str
    project_id: int
    project_name: str
    screening_version: int
    report_version: int
    generated_at: datetime
    jurisdiction: str
    knowledge_base_version: str
    taxonomy_version: str
    rules_version: str
    llm_provider: str
    review_state: ReviewState
    disclaimer: str = DISCLAIMER
    sections: list[ReportSection]
    status_label: str = "PRELIMINARY - AI-ASSISTED SCREENING - NOT AN APPROVED EIA"

    @field_validator("disclaimer")
    @classmethod
    def _disclaimer_unchanged(cls, v: str) -> str:
        if v != DISCLAIMER:
            raise ValueError("The mandatory disclaimer must not be altered")
        return v


# --------------------------------------------------------- Human review


class FindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["impact", "requirement", "mitigation", "missing_information", "activity", "profile"]
    finding_id: str
    verification_status: VerificationStatus | None = None
    field: str | None = None
    new_value: Any = None
    comment: str | None = Field(default=None, max_length=4000)


class ReviewAction(str, Enum):
    comment = "comment"
    submit_for_review = "submit_for_review"
    request_changes = "request_changes"
    approve = "approve"
    reject = "reject"
    issue_final = "issue_final"
    request_further_analysis = "request_further_analysis"


class HumanApproval(BaseModel):
    """A reviewer's action on a screening assessment."""

    model_config = ConfigDict(extra="forbid")

    reviewer_name: str = Field(min_length=2, max_length=120)
    reviewer_role: str = Field(min_length=2, max_length=120)
    reviewer_organisation: str | None = Field(default=None, max_length=200)
    action: ReviewAction
    comments: str | None = Field(default=None, max_length=8000)
    unresolved_issues: list[str] = Field(default_factory=list)
    finding_updates: list[FindingUpdate] = Field(default_factory=list)
    confirm_not_regulatory_approval: bool = False


class ReviewRecord(BaseModel):
    id: int
    screening_run_id: int
    reviewer_name: str
    reviewer_role: str
    reviewer_organisation: str | None
    action: str
    previous_state: str
    new_state: str
    comments: str | None
    unresolved_issues: list[str]
    created_at: datetime


# -------------------------------------------------------------- Workflow


class StageRecord(BaseModel):
    stage: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    message: str | None = None


class WorkflowResult(BaseModel):
    project_id: int
    screening_run_id: int
    version: int
    status: Literal["completed", "failed"]
    review_state: ReviewState
    stages: list[StageRecord]
    profile: ProjectProfile | None = None
    impacts: list[PotentialImpact] = Field(default_factory=list)
    requirements: list[RegulatoryRequirement] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    assessment: ScreeningAssessment | None = None
    mitigation: list[MitigationMeasure] = Field(default_factory=list)
    investigations: list[FurtherInvestigation] = Field(default_factory=list)
    validation: ValidationReport | None = None
    knowledge_base_version: str | None = None
    llm_provider: str | None = None
    error: str | None = None


# ----------------------------------------------------------- Regulations


class RegulationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{2,80}$")
    title: str = Field(min_length=3, max_length=400)
    short_title: str | None = Field(default=None, max_length=120)
    issuing_authority: str = Field(min_length=2, max_length=300)
    jurisdiction: str = Field(min_length=2, max_length=40)
    document_type: DocumentType
    content_form: ContentForm = ContentForm.official_text
    publication_date: str | None = None
    effective_date: str | None = None
    version: str = Field(min_length=1, max_length=120)
    source_url: str | None = Field(default=None, max_length=500)
    source_url_note: str | None = None
    applicable_sectors: list[str] = Field(default_factory=lambda: ["all"])
    date_last_verified: str | None = None
    legal_status: str | None = None
    status_verified: bool = False
    supersedes: str | None = None
    official_counterpart: str | None = None


class RegulationOut(BaseModel):
    id: int
    document_id: str
    title: str
    short_title: str | None
    issuing_authority: str
    jurisdiction: str
    document_type: str
    content_form: str
    binding: bool
    publication_date: str | None
    effective_date: str | None
    version: str
    source_url: str | None
    applicable_sectors: list[str]
    date_last_verified: str | None
    legal_status: str | None
    status_verified: bool
    status: str
    superseded_by: str | None
    official_counterpart: str | None = None
    extraction_method: str = "text_layer"
    chunk_count: int
    checksum: str
    ingested_at: datetime


class RegulationSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    jurisdiction: str | None = None
    include_related: bool = True
    top_k: int = Field(default=6, ge=1, le=30)


class RegulationSearchHit(BaseModel):
    chunk_id: int
    document_id: str
    title: str
    section: str | None
    page: int | None
    jurisdiction: str
    relationship: str
    document_type: str
    content_form: str
    score: float
    text: str
