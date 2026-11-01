"""Shared API helpers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ScreeningRun
from app.schemas import Coordinates, ProjectDetail, ProjectSummary, ReviewState


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def get_run_or_404(db: Session, project_id: int, version: int | None = None) -> ScreeningRun:
    q = select(ScreeningRun).where(ScreeningRun.project_id == project_id)
    if version is not None:
        q = q.where(ScreeningRun.version == version)
    else:
        q = q.where(ScreeningRun.status == "completed").order_by(ScreeningRun.version.desc()).limit(1)
    run = db.scalar(q)
    if run is None:
        detail = (f"Screening version {version} not found" if version is not None
                  else "No completed screening for this project - run POST /projects/{id}/screening first")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail)
    return run


def latest_version(project: Project) -> int | None:
    done = [r.version for r in project.screening_runs if r.status == "completed"]
    return max(done) if done else None


def project_summary(p: Project) -> ProjectSummary:
    return ProjectSummary(id=p.id, name=p.name, developer=p.developer, sector=p.sector, jurisdiction=p.jurisdiction,
                          region=p.region, review_state=ReviewState(p.review_state), created_at=p.created_at,
                          latest_screening_version=latest_version(p))


def project_detail(p: Project) -> ProjectDetail:
    coords = Coordinates(latitude=p.latitude, longitude=p.longitude) if p.latitude is not None else None
    docs = [{"id": d.id, "filename": d.original_filename, "file_type": d.file_type, "size_bytes": d.size_bytes,
             "category": d.document_category, "page_count": d.page_count, "injection_flags": d.injection_flags,
             "uploaded_at": d.uploaded_at.isoformat()} for d in p.documents]
    return ProjectDetail(**project_summary(p).model_dump(), description=p.description, phase=p.phase,
                         objectives=p.objectives, location=p.location, coordinates=coords,
                         characteristics=p.characteristics or {}, environmental_setting=p.environmental_setting,
                         documents=docs, profile=p.profile_json)
