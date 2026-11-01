"""Read access to the findings of a screening run, including reviewer edits."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActivityRecord,
    EvidenceRecord,
    HumanReview,
    ImpactRecord,
    MissingInformationRecord,
    MitigationRecord,
    Project,
    RequirementRecord,
    ScreeningRun,
)

FINDING_MODELS = {
    "activity": ActivityRecord,
    "impact": ImpactRecord,
    "requirement": RequirementRecord,
    "mitigation": MitigationRecord,
    "missing_information": MissingInformationRecord,
}


def _rows(db: Session, model, run_id: int) -> list[dict[str, Any]]:
    out = []
    for rec in db.scalars(select(model).where(model.screening_run_id == run_id).order_by(model.id)):
        item = dict(rec.data)
        item["verification_status"] = rec.verification_status
        item["reviewer_comment"] = rec.reviewer_comment
        item["_finding_id"] = rec.finding_id
        out.append(item)
    return out


def latest_run(db: Session, project_id: int, completed_only: bool = True) -> ScreeningRun | None:
    q = select(ScreeningRun).where(ScreeningRun.project_id == project_id)
    if completed_only:
        q = q.where(ScreeningRun.status == "completed")
    return db.scalar(q.order_by(ScreeningRun.version.desc()).limit(1))


def load_findings(db: Session, run: ScreeningRun) -> dict[str, Any]:
    evidence = [dict(e.data, verified=e.verified) for e in
                db.scalars(select(EvidenceRecord).where(EvidenceRecord.screening_run_id == run.id).order_by(EvidenceRecord.id))]
    reviews = db.scalars(select(HumanReview).where(HumanReview.screening_run_id == run.id).order_by(HumanReview.id)).all()
    return {
        "run": run,
        "project": db.get(Project, run.project_id),
        "profile": run.profile_json or {},
        "assessment": run.assessment_json or {},
        "validation": run.validation_json or {},
        "investigations": run.investigations_json or [],
        "narrative": run.narrative_json or {},
        "activities": _rows(db, ActivityRecord, run.id),
        "impacts": _rows(db, ImpactRecord, run.id),
        "requirements": _rows(db, RequirementRecord, run.id),
        "mitigation": _rows(db, MitigationRecord, run.id),
        "missing_information": _rows(db, MissingInformationRecord, run.id),
        "evidence": evidence,
        "reviews": [{"reviewer_name": r.reviewer_name, "reviewer_role": r.reviewer_role,
                     "reviewer_organisation": r.reviewer_organisation, "action": r.action,
                     "previous_state": r.previous_state, "new_state": r.new_state, "comments": r.comments,
                     "unresolved_issues": r.unresolved_issues, "created_at": r.created_at.isoformat()} for r in reviews],
    }
