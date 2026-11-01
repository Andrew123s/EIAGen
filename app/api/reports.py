"""Report retrieval, regeneration and export."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404, get_run_or_404
from app.core.security import require_api_key
from app.database import get_db
from app.models import Report
from app.services.report_service import generate_report

router = APIRouter(prefix="/projects", tags=["reports"])

MEDIA = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "pdf": "application/pdf", "md": "text/markdown"}


def _report(db: Session, project_id: int, version: int | None) -> Report:
    q = select(Report).where(Report.project_id == project_id)
    q = q.where(Report.version == version) if version is not None else q.order_by(Report.version.desc()).limit(1)
    report = db.scalar(q)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No report found - run a screening first")
    return report


@router.get("/{project_id}/report")
def get_report(project_id: int, version: int | None = None, db: Session = Depends(get_db)) -> dict:
    get_project_or_404(db, project_id)
    r = _report(db, project_id, version)
    return {"report_version": r.version, "screening_run_id": r.screening_run_id, "generated_at": r.generated_at.isoformat(),
            "review_state_at_generation": r.review_state_at_generation,
            "knowledge_base_version": r.knowledge_base_version, "content": r.content_json,
            "exports": {fmt: f"/projects/{project_id}/report/export?format={fmt}&version={r.version}"
                        for fmt in ("docx", "pdf", "md", "json")}}


@router.post("/{project_id}/report", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_key)])
def regenerate_report(project_id: int, db: Session = Depends(get_db)) -> dict:
    get_project_or_404(db, project_id)
    run = get_run_or_404(db, project_id)
    report = generate_report(db, run)
    db.commit()
    return {"report_version": report.version, "screening_version": run.version}


@router.get("/{project_id}/report/export")
def export_report(project_id: int, format: str = Query("pdf", pattern="^(docx|pdf|md|json)$"),
                  version: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    r = _report(db, project_id, version)
    if format == "json":
        return r.content_json
    path = {"docx": r.docx_path, "pdf": r.pdf_path, "md": r.markdown_path}[format]
    if not path or not Path(path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{format.upper()} file not available")
    return FileResponse(path, media_type=MEDIA[format], filename=Path(path).name)
