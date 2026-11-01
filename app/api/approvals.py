"""Human review endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404, get_run_or_404
from app.core.security import require_api_key
from app.database import get_db
from app.models import HumanReview
from app.schemas import HumanApproval, ReviewRecord
from app.services.review_service import ReviewError, submit_review

router = APIRouter(prefix="/projects", tags=["human review"])

NOTICE = ("Human review within EIAGen is a professional review of a preliminary screening. It is not a regulatory "
          "approval, environmental permit or EIA determination.")


def _record(r: HumanReview) -> ReviewRecord:
    return ReviewRecord(id=r.id, screening_run_id=r.screening_run_id, reviewer_name=r.reviewer_name,
                        reviewer_role=r.reviewer_role, reviewer_organisation=r.reviewer_organisation, action=r.action,
                        previous_state=r.previous_state, new_state=r.new_state, comments=r.comments,
                        unresolved_issues=r.unresolved_issues or [], created_at=r.created_at)


@router.post("/{project_id}/review", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_key)])
def review(project_id: int, payload: HumanApproval, version: int | None = None, db: Session = Depends(get_db)) -> dict:
    project = get_project_or_404(db, project_id)
    run = get_run_or_404(db, project_id, version)
    try:
        record = submit_review(db, project, run, payload)
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"review": _record(record).model_dump(mode="json"), "review_state": run.review_state, "notice": NOTICE}


@router.get("/{project_id}/reviews", response_model=list[ReviewRecord])
def list_reviews(project_id: int, db: Session = Depends(get_db)) -> list[ReviewRecord]:
    get_project_or_404(db, project_id)
    rows = db.scalars(select(HumanReview).where(HumanReview.project_id == project_id).order_by(HumanReview.id)).all()
    return [_record(r) for r in rows]
