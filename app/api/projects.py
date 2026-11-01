"""Project registration, documents, analysis and profile correction."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import analyse_project
from app.api.deps import get_project_or_404, project_detail, project_summary
from app.core.config import get_settings
from app.core.security import UploadValidationError, detect_injection, require_api_key, safe_filename, validate_upload
from app.database import get_db
from app.models import AuditLog, Project, ProjectDocument, ScreeningRun
from app.schemas import ProfileCorrection, ProjectCreate, ProjectDetail, ProjectProfile, ProjectSummary
from app.services import audit
from app.services.analysis_service import apply_corrections
from app.services.document_processor import extract_pages, sha256_bytes
from app.services.jurisdictions import is_supported

router = APIRouter(prefix="/projects", tags=["projects"])

DOCUMENT_CATEGORIES = {"project_description", "feasibility_study", "preliminary_design", "baseline_report", "map",
                       "technical_specification", "existing_eia", "regulatory_correspondence", "other"}


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key)])
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectDetail:
    if not is_supported(payload.jurisdiction):
        raise HTTPException(422,
                            f"Unsupported jurisdiction '{payload.jurisdiction}'. Add it to config/jurisdictions.yaml "
                            "with its own regulatory collection.")
    project = Project(
        name=payload.name.strip(), developer=payload.developer, description=payload.description,
        sector=payload.sector, phase=payload.phase, objectives=payload.objectives, location=payload.location,
        jurisdiction=payload.jurisdiction, region=payload.region,
        latitude=payload.coordinates.latitude if payload.coordinates else None,
        longitude=payload.coordinates.longitude if payload.coordinates else None,
        characteristics=payload.characteristics, environmental_setting=payload.environmental_setting)
    db.add(project)
    db.flush()
    audit.record(db, actor="user", actor_type="human", action="project_registered", entity_type="project",
                 entity_id=str(project.id), project_id=project.id, new_value={"name": project.name})
    db.commit()
    db.refresh(project)
    return project_detail(project)


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectSummary]:
    return [project_summary(p) for p in db.scalars(select(Project).order_by(Project.id.desc()))]


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectDetail:
    return project_detail(get_project_or_404(db, project_id))


@router.post("/{project_id}/documents", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_key)])
async def upload_document(project_id: int, file: UploadFile = File(...),
                          category: str = Form("project_description"), db: Session = Depends(get_db)) -> dict:
    project = get_project_or_404(db, project_id)
    if category not in DOCUMENT_CATEGORIES:
        raise HTTPException(422, f"Unknown category; use one of {sorted(DOCUMENT_CATEGORIES)}")
    settings = get_settings()
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        ext = validate_upload(file.filename or "", content)
    except UploadValidationError as exc:
        code = 413 if "limit" in str(exc) else \
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE if "not allowed" in str(exc) or "match" in str(exc) else \
            status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    dest_dir = settings.uploads_dir / f"project_{project.id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(file.filename or f"upload{ext}")
    dest.write_bytes(content)
    try:
        pages = extract_pages(dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"Could not extract text: {type(exc).__name__}") from exc
    text = "\n".join(p.text for p in pages)
    flags = detect_injection(text)
    doc = ProjectDocument(project_id=project.id, original_filename=(file.filename or "")[:255], stored_path=str(dest),
                          file_type=ext.lstrip("."), size_bytes=len(content), checksum=sha256_bytes(content),
                          document_category=category, extracted_text=text,
                          pages=[{"page": p.page, "text": p.text} for p in pages], page_count=len(pages),
                          injection_flags=[f[:200] for f in flags])
    db.add(doc)
    db.flush()
    audit.record(db, actor="user", actor_type="human", action="document_uploaded", entity_type="project_document",
                 entity_id=str(doc.id), project_id=project.id,
                 new_value={"filename": doc.original_filename, "pages": doc.page_count, "injection_flags": len(flags)})
    db.commit()
    return {"id": doc.id, "filename": doc.original_filename, "file_type": doc.file_type, "size_bytes": doc.size_bytes,
            "page_count": doc.page_count, "characters_extracted": len(text), "category": category,
            "injection_flags": doc.injection_flags,
            "note": "Document content is treated as untrusted data; instructions inside it are ignored."}


@router.post("/{project_id}/analyze", response_model=ProjectProfile, dependencies=[Depends(require_api_key)])
def analyze_project(project_id: int, db: Session = Depends(get_db)) -> ProjectProfile:
    return analyse_project(db, get_project_or_404(db, project_id))


@router.get("/{project_id}/profile", response_model=ProjectProfile)
def get_profile(project_id: int, db: Session = Depends(get_db)) -> ProjectProfile:
    project = get_project_or_404(db, project_id)
    if not project.profile_json:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not analysed yet - POST /projects/{id}/analyze")
    return ProjectProfile.model_validate(project.profile_json)


@router.patch("/{project_id}/profile", response_model=ProjectProfile, dependencies=[Depends(require_api_key)])
def correct_profile(project_id: int, correction: ProfileCorrection, db: Session = Depends(get_db)) -> ProjectProfile:
    project = get_project_or_404(db, project_id)
    profile = ProjectProfile.model_validate(project.profile_json) if project.profile_json else analyse_project(db, project)
    try:
        return apply_corrections(db, project, profile, correction)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/{project_id}/missing-information")
def missing_information(project_id: int, db: Session = Depends(get_db)) -> dict:
    project = get_project_or_404(db, project_id)
    run = db.scalar(select(ScreeningRun).where(ScreeningRun.project_id == project_id, ScreeningRun.status == "completed")
                    .order_by(ScreeningRun.version.desc()).limit(1))
    profile = (run.profile_json if run else None) or project.profile_json
    if not profile:
        profile = analyse_project(db, project).model_dump(mode="json")
    return {"source": f"screening version {run.version}" if run else "project analysis",
            "missing_information": profile["missing_information"],
            "clarification_questions": profile["clarification_questions"]}


@router.get("/{project_id}/history")
def assessment_history(project_id: int, db: Session = Depends(get_db)) -> dict:
    project = get_project_or_404(db, project_id)
    runs = []
    for r in project.screening_runs:
        runs.append({"screening_run_id": r.id, "version": r.version, "status": r.status, "review_state": r.review_state,
                     "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                     "knowledge_base_version": r.knowledge_base_version, "llm_provider": r.llm_provider,
                     "outcomes": (r.assessment_json or {}).get("outcome_labels", []), "error": r.error})
    return {"project_id": project_id, "runs": runs}


@router.get("/{project_id}/audit")
def audit_trail(project_id: int, db: Session = Depends(get_db)) -> list[dict]:
    get_project_or_404(db, project_id)
    rows = db.scalars(select(AuditLog).where(AuditLog.project_id == project_id).order_by(AuditLog.id)).all()
    return [{"id": a.id, "timestamp": a.created_at.isoformat(), "actor": a.actor, "actor_type": a.actor_type,
             "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "field": a.field,
             "previous_value": a.previous_value, "new_value": a.new_value, "comment": a.comment,
             "screening_run_id": a.screening_run_id} for a in rows]
