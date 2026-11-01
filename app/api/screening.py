"""Screening workflow, findings and evidence endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import run_screening
from app.api.deps import get_project_or_404, get_run_or_404
from app.core.security import require_api_key
from app.database import get_db
from app.models import RegulatoryChunk, ScreeningRun, ToolExecution
from app.schemas import WorkflowResult
from app.services.findings_service import load_findings

router = APIRouter(prefix="/projects", tags=["screening"])


@router.post("/{project_id}/screening", response_model=WorkflowResult, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key)])
def start_screening(project_id: int, use_saved_profile: bool = True, db: Session = Depends(get_db)) -> WorkflowResult:
    project = get_project_or_404(db, project_id)
    result = run_screening(db, project, use_saved_profile=use_saved_profile)
    if result.status != "completed":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            {"message": "Screening workflow failed", "error": result.error,
                             "stages": [s.model_dump(mode="json") for s in result.stages]})
    return result


@router.get("/{project_id}/screening")
def get_screening(project_id: int, version: int | None = Query(None), db: Session = Depends(get_db)) -> dict:
    run = get_run_or_404(db, project_id, version)
    f = load_findings(db, run)
    return {"project_id": project_id, "screening_run_id": run.id, "version": run.version, "status": run.status,
            "review_state": run.review_state, "knowledge_base_version": run.knowledge_base_version,
            "llm_provider": run.llm_provider, "assessment": run.assessment_json, "profile": run.profile_json,
            "executive_summary": (run.narrative_json or {}).get("executive_summary"),
            "mitigation": f["mitigation"], "investigations": f["investigations"],
            "validation": run.validation_json, "notice": "Preliminary AI-assisted screening; requires human review."}


@router.get("/{project_id}/screening/status")
def screening_status(project_id: int, db: Session = Depends(get_db)) -> dict:
    get_project_or_404(db, project_id)
    run = db.scalar(select(ScreeningRun).where(ScreeningRun.project_id == project_id)
                    .order_by(ScreeningRun.version.desc()).limit(1))
    if run is None:
        return {"project_id": project_id, "status": "not_started", "stages": []}
    tools = db.scalars(select(ToolExecution).where(ToolExecution.screening_run_id == run.id).order_by(ToolExecution.id)).all()
    return {"project_id": project_id, "version": run.version, "status": run.status, "review_state": run.review_state,
            "stages": run.stages_json or [], "error": run.error,
            "tool_executions": [{"tool": t.tool_name, "stage": t.stage, "status": t.status, "duration_ms": t.duration_ms}
                                for t in tools]}


@router.get("/{project_id}/impacts")
def get_impacts(project_id: int, version: int | None = None, component: str | None = None,
                db: Session = Depends(get_db)) -> list[dict]:
    items = load_findings(db, get_run_or_404(db, project_id, version))["impacts"]
    return [i for i in items if component is None or i["environmental_component"] == component]


@router.get("/{project_id}/regulations")
def get_requirements(project_id: int, version: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return load_findings(db, get_run_or_404(db, project_id, version))["requirements"]


@router.get("/{project_id}/mitigation")
def get_mitigation(project_id: int, version: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return load_findings(db, get_run_or_404(db, project_id, version))["mitigation"]


@router.get("/{project_id}/evidence")
def get_evidence(project_id: int, version: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return load_findings(db, get_run_or_404(db, project_id, version))["evidence"]


@router.get("/{project_id}/evidence/{citation_key}")
def inspect_evidence(project_id: int, citation_key: str, version: int | None = None, db: Session = Depends(get_db)) -> dict:
    """Return a citation together with the full original stored passage."""
    f = load_findings(db, get_run_or_404(db, project_id, version))
    ref = next((e for e in f["evidence"] if e["citation_key"] == citation_key), None)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Citation {citation_key} not found")
    original = None
    if ref.get("chunk_id") is not None:
        chunk = db.get(RegulatoryChunk, ref["chunk_id"])
        original = chunk.text if chunk else None
    else:
        project = f["project"]
        for doc in project.documents:
            if ref["document_id"] == f"projdoc-{doc.id}":
                original = next((p["text"] for p in (doc.pages or []) if p.get("page") == ref.get("page")), None)
    return {"citation": ref, "original_passage": original}
