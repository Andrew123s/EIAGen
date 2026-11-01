"""Human-in-the-loop review workflow.

States: draft -> ai_analysis_completed -> awaiting_human_review ->
        (changes_requested | human_reviewed) -> final_preliminary_report_issued

Rules enforced here:
* reviewer identity is mandatory; reserved names (system, ai, agent, ...) are
  refused, so the AI pipeline can never approve its own assessment
* only the listed transitions are possible
* editing findings after approval returns the run to "awaiting human review"
* an issued final preliminary report is locked; changes need a new screening run
* issuing requires explicit confirmation that this is NOT a regulatory approval
* every change records reviewer, timestamp, previous and new values
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import HumanReview, Project, ScreeningRun
from app.schemas import (
    ApplicabilityStatus,
    FindingUpdate,
    HumanApproval,
    Provenance,
    ReviewAction,
    ReviewState,
    VerificationStatus,
)
from app.services import audit
from app.services.findings_service import FINDING_MODELS

S = ReviewState
TRANSITIONS: dict[ReviewAction, tuple[set[ReviewState], ReviewState | None]] = {
    ReviewAction.comment: (set(S) - {S.final_preliminary_report_issued}, None),
    ReviewAction.submit_for_review: ({S.ai_analysis_completed, S.changes_requested}, S.awaiting_human_review),
    ReviewAction.request_changes: ({S.awaiting_human_review, S.human_reviewed}, S.changes_requested),
    ReviewAction.request_further_analysis: ({S.awaiting_human_review, S.human_reviewed}, S.changes_requested),
    ReviewAction.reject: ({S.awaiting_human_review, S.human_reviewed}, S.changes_requested),
    ReviewAction.approve: ({S.awaiting_human_review}, S.human_reviewed),
    ReviewAction.issue_final: ({S.human_reviewed}, S.final_preliminary_report_issued),
}
EDITABLE_STATES = {S.ai_analysis_completed, S.awaiting_human_review, S.changes_requested, S.human_reviewed}
EDITABLE_FIELDS = {
    "impact": {"likelihood", "severity", "confidence", "potential_receptor", "impact_pathway", "duration",
               "geographical_extent", "reversibility", "additional_information_required"},
    "requirement": {"applicability", "potential_obligation", "permit_or_approval", "uncertainty"},
    "mitigation": {"measure", "hierarchy_level", "measure_type"},
    "missing_information": {"description", "clarification_question", "importance"},
    "activity": {"phases", "name"},
}


class ReviewError(ValueError):
    status_code = 400


class ReviewForbidden(ReviewError):
    status_code = 403


class ReviewConflict(ReviewError):
    status_code = 409


def check_reviewer(name: str) -> None:
    reserved = {n.lower() for n in get_settings().reserved_reviewer_names}
    norm = name.strip().lower()
    if not norm or norm in reserved or any(norm.startswith(r + " ") or norm.endswith(" " + r) for r in reserved):
        raise ReviewForbidden("Reviewer identity must be a named human reviewer; the AI system cannot review or "
                              "approve its own assessment")


def _apply_update(db: Session, run: ScreeningRun, upd: FindingUpdate, reviewer: str) -> None:
    if upd.finding_type == "profile":
        raise ReviewError("Profile corrections are made through the project-profile endpoint before screening")
    model = FINDING_MODELS[upd.finding_type]
    rec = db.scalar(select(model).where(model.screening_run_id == run.id, model.finding_id == upd.finding_id))
    if rec is None:
        raise ReviewError(f"{upd.finding_type} '{upd.finding_id}' not found in screening version {run.version}")
    common = dict(actor=reviewer, actor_type="human", entity_type=upd.finding_type, entity_id=upd.finding_id,
                  project_id=run.project_id, screening_run_id=run.id)
    if upd.field is not None:
        if upd.field not in EDITABLE_FIELDS.get(upd.finding_type, set()):
            raise ReviewError(f"Field '{upd.field}' of {upd.finding_type} is not editable")
        if upd.finding_type == "requirement" and upd.field == "applicability":
            try:
                ApplicabilityStatus(upd.new_value)  # controlled vocabulary only
            except ValueError as exc:
                raise ReviewError(f"Applicability must be one of {[s.value for s in ApplicabilityStatus]}") from exc
        data = dict(rec.data)
        previous = data.get(upd.field)
        data[upd.field] = upd.new_value
        data["provenance_note"] = f"{upd.field} corrected by human reviewer {reviewer}"
        if upd.finding_type == "requirement":
            data["provenance"] = Provenance.human_review.value
            if upd.field == "applicability":
                rec.applicability = upd.new_value
        rec.data = data
        if rec.verification_status == VerificationStatus.unverified.value:
            rec.verification_status = VerificationStatus.corrected.value
        audit.record(db, action="finding_corrected", field=upd.field, previous_value=previous,
                     new_value=upd.new_value, comment=upd.comment, **common)
    if upd.verification_status is not None:
        previous = rec.verification_status
        rec.verification_status = upd.verification_status.value
        audit.record(db, action="finding_verification_changed", field="verification_status",
                     previous_value=previous, new_value=upd.verification_status.value, comment=upd.comment, **common)
    if upd.comment:
        rec.reviewer_comment = upd.comment


def submit_review(db: Session, project: Project, run: ScreeningRun, req: HumanApproval) -> HumanReview:
    check_reviewer(req.reviewer_name)
    state = ReviewState(run.review_state)
    if state == S.final_preliminary_report_issued:
        raise ReviewConflict("The final preliminary report has been issued and is locked. Run a new screening to "
                             "create a new assessment version.")
    allowed_from, target = TRANSITIONS[req.action]
    if state not in allowed_from:
        raise ReviewConflict(f"Action '{req.action.value}' is not allowed from state '{state.value}'")
    if req.action == ReviewAction.issue_final and not req.confirm_not_regulatory_approval:
        raise ReviewError("Issuing requires confirm_not_regulatory_approval=true: the issued document is a "
                          "preliminary screening report, not a regulatory approval or permit")
    if req.action in (ReviewAction.request_changes, ReviewAction.reject, ReviewAction.request_further_analysis) \
            and not (req.comments or req.unresolved_issues):
        raise ReviewError("Please explain the requested changes in comments or unresolved_issues")

    if req.finding_updates:
        if state not in EDITABLE_STATES:
            raise ReviewConflict(f"Findings cannot be edited in state '{state.value}'")
        for upd in req.finding_updates:
            _apply_update(db, run, upd, req.reviewer_name)

    new_state = target or state
    if target is None and req.finding_updates and state == S.human_reviewed:
        new_state = S.awaiting_human_review   # edits after approval invalidate the approval
    review = HumanReview(project_id=project.id, screening_run_id=run.id, reviewer_name=req.reviewer_name.strip(),
                         reviewer_role=req.reviewer_role.strip(), reviewer_organisation=req.reviewer_organisation,
                         action=req.action.value, previous_state=state.value, new_state=new_state.value,
                         comments=req.comments, unresolved_issues=req.unresolved_issues)
    db.add(review)
    if new_state != state:
        run.review_state = new_state.value
        audit.record(db, actor=req.reviewer_name, actor_type="human", action="state_changed",
                     entity_type="screening_run", entity_id=str(run.id), project_id=project.id,
                     screening_run_id=run.id, field="review_state", previous_value=state.value,
                     new_value=new_state.value, comment=req.comments)
    latest = db.scalar(select(ScreeningRun).where(ScreeningRun.project_id == project.id,
                                                  ScreeningRun.status == "completed")
                       .order_by(ScreeningRun.version.desc()).limit(1))
    if latest is not None and latest.id == run.id:
        project.review_state = run.review_state
    db.flush()
    if req.action in (ReviewAction.approve, ReviewAction.issue_final) or req.finding_updates:
        from app.services.report_service import generate_report

        generate_report(db, run)   # regenerate so the report reflects the review status
    db.commit()
    return review
