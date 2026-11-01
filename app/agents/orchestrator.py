"""Controlled multi-step screening workflow.

    PROJECT DESCRIPTION
      -> project_information_extraction       (Agent 1, tool: extract_project_profile [+ LLM supplement])
      -> project_profile_validation           (Pydantic re-validation; reject malformed profile)
      -> missing_information_check            (tool: identify_missing_information)
      -> environmental_impact_identification  (Agent 2, tool: identify_environmental_impacts)
      -> regulatory_requirement_retrieval     (Agent 3, tool: retrieve_regulatory_provisions - RAG)
      -> preliminary_eia_screening            (Agent 4, tool: evaluate_screening_criteria - deterministic)
      -> evidence_citation_validation         (Agent 5, tool: validate_citations + rule checks)
      -> preliminary_screening_report         (Agent 6 + report service)
      -> human_review_and_approval            (left pending: only a human can complete it)

Each stage has a fixed order, validated inputs/outputs and a timing record. No
stage can be skipped and the workflow never sets a human-review state itself
beyond "awaiting human review".
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import (
    impact_assessment_agent,
    project_analysis_agent,
    regulatory_retrieval_agent,
    report_generation_agent,
    screening_agent,
    validation_agent,
)
from app.core.logging import get_logger
from app.llm.providers import get_llm_provider
from app.models import (
    ActivityRecord,
    EvidenceRecord,
    ImpactRecord,
    MissingInformationRecord,
    MitigationRecord,
    Project,
    RequirementRecord,
    ScreeningRun,
)
from app.schemas import (
    ProjectProfile,
    ReviewState,
    StageRecord,
    WorkflowResult,
)
from app.services import audit
from app.services.analysis_service import build_project_input
from app.services.citation_service import CitationRegistry
from app.services.regulatory_library import knowledge_base_version
from app.services.taxonomy import rules, taxonomy
from app.tools import ToolContext, build_registry

log = get_logger("orchestrator")

STAGES = [
    "project_information_extraction",
    "project_profile_validation",
    "missing_information_check",
    "environmental_impact_identification",
    "regulatory_requirement_retrieval",
    "preliminary_eia_screening",
    "evidence_citation_validation",
    "preliminary_screening_report",
    "human_review_and_approval",
]


class WorkflowError(RuntimeError):
    pass


class _StageTracker:
    def __init__(self) -> None:
        self.records = {s: StageRecord(stage=s, status="pending") for s in STAGES}
        self._t0: float = 0.0

    def start(self, stage: str) -> None:
        self.records[stage].status = "running"
        self.records[stage].started_at = datetime.now(timezone.utc)
        self._t0 = time.perf_counter()

    def finish(self, stage: str, message: str | None = None, status: str = "completed") -> None:
        rec = self.records[stage]
        rec.status = status  # type: ignore[assignment]
        rec.finished_at = datetime.now(timezone.utc)
        rec.duration_ms = round((time.perf_counter() - self._t0) * 1000, 1)
        rec.message = message

    def as_list(self) -> list[StageRecord]:
        return list(self.records.values())


def analyse_project(db: Session, project: Project) -> ProjectProfile:
    """Run only the project-analysis stage (for review and correction before screening)."""
    registry = build_registry()
    ctx = ToolContext(db=db, project_id=project.id, stage="project_information_extraction",
                      state={"project_input": build_project_input(project), "project": project})
    profile = project_analysis_agent.run(ctx, registry, get_llm_provider())
    profile = ProjectProfile.model_validate(profile.model_dump())
    project.profile_json = profile.model_dump(mode="json")
    project.profile_corrected_by = None
    audit.record(db, actor="system", actor_type="system", action="project_analysed", entity_type="project",
                 entity_id=str(project.id), project_id=project.id,
                 new_value={"activities": len(profile.activities), "missing": len(profile.missing_information)})
    db.commit()
    return profile


def run_screening(db: Session, project: Project, use_saved_profile: bool = True) -> WorkflowResult:
    llm = get_llm_provider()
    registry = build_registry()
    tracker = _StageTracker()
    version = (db.scalar(select(func.max(ScreeningRun.version)).where(ScreeningRun.project_id == project.id)) or 0) + 1
    run = ScreeningRun(project_id=project.id, version=version, status="running", review_state=ReviewState.draft.value,
                       knowledge_base_version=knowledge_base_version(db), llm_provider=llm.name,
                       taxonomy_version=taxonomy()["taxonomy_version"], rules_version=rules()["rules_version"])
    db.add(run)
    db.flush()
    inp = build_project_input(project)
    citations = CitationRegistry()
    ctx = ToolContext(db=db, project_id=project.id, screening_run_id=run.id,
                      state={"project_input": inp, "project": project, "citations": citations})
    project_text = " ".join(s.text for s in inp.sources())
    result = WorkflowResult(project_id=project.id, screening_run_id=run.id, version=version, status="failed",
                            review_state=ReviewState.draft, stages=tracker.as_list(),
                            knowledge_base_version=run.knowledge_base_version, llm_provider=llm.name)
    stage = STAGES[0]
    try:
        # 1. Extraction ---------------------------------------------------
        stage = "project_information_extraction"
        tracker.start(stage)
        ctx.stage = stage
        if use_saved_profile and project.profile_json and project.profile_corrected_by:
            profile = ProjectProfile.model_validate(project.profile_json)
            tracker.finish(stage, f"Using profile corrected by {project.profile_corrected_by}")
        else:
            profile = project_analysis_agent.run(ctx, registry, llm)
            tracker.finish(stage, f"{len(profile.activities)} activities, {len(profile.parameters)} parameters")

        # 2. Profile validation -------------------------------------------
        stage = "project_profile_validation"
        tracker.start(stage)
        try:
            profile = ProjectProfile.model_validate(profile.model_dump())
        except ValidationError as exc:
            raise WorkflowError(f"Project profile failed validation ({exc.error_count()} errors)") from exc
        if profile.jurisdiction != project.jurisdiction:
            raise WorkflowError("Profile jurisdiction does not match the project jurisdiction")
        tracker.finish(stage, "Profile schema valid")

        # 3. Missing information --------------------------------------------
        stage = "missing_information_check"
        tracker.start(stage)
        ctx.stage = stage
        missing = registry.invoke("identify_missing_information", profile, ctx)
        crit = sum(1 for m in profile.missing_information if m.importance == "critical")
        tracker.finish(stage, f"{len(missing.missing_information)} gaps ({crit} critical)")  # type: ignore[attr-defined]

        # 4. Impacts -------------------------------------------------------
        stage = "environmental_impact_identification"
        tracker.start(stage)
        ctx.stage = stage
        if not profile.activities:
            impacts = []
            tracker.finish(stage, "No activities identified - impact identification not possible", status="skipped")
        else:
            impacts = impact_assessment_agent.run(ctx, registry, profile)
            tracker.finish(stage, f"{len(impacts)} potential impacts")

        # 5. Regulatory retrieval ---------------------------------------------
        stage = "regulatory_requirement_retrieval"
        tracker.start(stage)
        ctx.stage = stage
        requirements, _ = regulatory_retrieval_agent.run(ctx, registry, profile, impacts)
        tracker.finish(stage, f"{len(requirements)} provisions retrieved")

        # 6. Screening ------------------------------------------------------
        stage = "preliminary_eia_screening"
        tracker.start(stage)
        ctx.stage = stage
        assessment, mitigation, investigations = screening_agent.run(
            ctx, registry, profile, impacts, requirements, project_text)
        tracker.finish(stage, "; ".join(assessment.outcome_labels))

        # 7. Validation -----------------------------------------------------
        stage = "evidence_citation_validation"
        tracker.start(stage)
        ctx.stage = stage
        narrative = report_generation_agent.draft_summary(llm, profile, impacts, requirements, assessment,
                                                          list(citations.items))
        evidence = list(citations.items)
        validation, trusted = validation_agent.run(ctx, registry, profile, impacts, requirements, evidence,
                                                   assessment, [narrative["executive_summary"]])
        tracker.finish(stage, f"{validation.citations_valid}/{validation.citations_checked} citations valid; "
                              f"{len(validation.unsupported_claims)} unsupported items flagged")

        # Persist validated findings only.
        _persist(db, run, profile, impacts, trusted, mitigation, evidence, assessment, validation,
                 investigations, narrative)
        run.status = "completed"
        run.review_state = ReviewState.ai_analysis_completed.value
        run.finished_at = datetime.now(timezone.utc)
        audit.record(db, actor="system", actor_type="system", action="screening_completed", entity_type="screening_run",
                     entity_id=str(run.id), project_id=project.id, screening_run_id=run.id,
                     new_value={"version": version, "outcomes": [o.value for o in assessment.outcomes]})

        # 8. Report -----------------------------------------------------------
        stage = "preliminary_screening_report"
        tracker.start(stage)
        from app.services.report_service import generate_report

        report = generate_report(db, run)
        prev_state = run.review_state
        run.review_state = ReviewState.awaiting_human_review.value
        project.review_state = run.review_state
        audit.record(db, actor="system", actor_type="system", action="state_changed", entity_type="screening_run",
                     entity_id=str(run.id), project_id=project.id, screening_run_id=run.id, field="review_state",
                     previous_value=prev_state, new_value=run.review_state)
        tracker.finish(stage, f"Report version {report.version} generated (DOCX, PDF, Markdown)")
        tracker.records["human_review_and_approval"].message = "Awaiting a qualified human reviewer"

        result = result.model_copy(update=dict(
            status="completed", review_state=ReviewState(run.review_state), profile=profile, impacts=impacts,
            requirements=trusted, evidence=evidence, assessment=assessment, mitigation=mitigation,
            investigations=investigations, validation=validation))
    except Exception as exc:
        log.exception("Screening failed at stage %s", stage)
        tracker.finish(stage, f"{type(exc).__name__}: {exc}"[:500], status="failed")
        run.status = "failed"
        run.error = f"{stage}: {type(exc).__name__}: {exc}"[:2000]
        run.finished_at = datetime.now(timezone.utc)
        result = result.model_copy(update={"status": "failed", "error": run.error})
    finally:
        run.stages_json = [s.model_dump(mode="json") for s in tracker.as_list()]
        result.stages = tracker.as_list()
        db.commit()
    return result


def _persist(db: Session, run: ScreeningRun, profile, impacts, requirements, mitigation, evidence, assessment,
             validation, investigations, narrative) -> None:
    run.profile_json = profile.model_dump(mode="json")
    run.assessment_json = assessment.model_dump(mode="json")
    run.validation_json = validation.model_dump(mode="json")
    run.investigations_json = [i.model_dump(mode="json") for i in investigations]
    run.narrative_json = narrative
    for a in profile.activities:
        db.add(ActivityRecord(screening_run_id=run.id, finding_id=a.activity_id, activity_key=a.key,
                              data=a.model_dump(mode="json")))
    for i in impacts:
        db.add(ImpactRecord(screening_run_id=run.id, finding_id=i.impact_id, component=i.environmental_component,
                            risk_priority=i.risk_priority, data=i.model_dump(mode="json")))
    for r in requirements:
        db.add(RequirementRecord(screening_run_id=run.id, finding_id=r.requirement_id, document_id=r.document_id,
                                 applicability=r.applicability.value, data=r.model_dump(mode="json")))
    for m in mitigation:
        db.add(MitigationRecord(screening_run_id=run.id, finding_id=m.measure_id, impact_id=m.impact_id,
                                data=m.model_dump(mode="json")))
    for n, m in enumerate(profile.missing_information, start=1):
        db.add(MissingInformationRecord(screening_run_id=run.id, finding_id=f"MISS-{n:02d}", importance=m.importance,
                                        data=m.model_dump(mode="json")))
    for e in evidence:
        db.add(EvidenceRecord(screening_run_id=run.id, citation_key=e.citation_key, source_type=e.source_type,
                              document_id=e.document_id, chunk_id=e.chunk_id, page=e.page, section=e.section,
                              passage=e.passage, verified=e.verified, data=e.model_dump(mode="json")))
    db.flush()
