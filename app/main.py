"""EIAGen FastAPI application.

Run:  uvicorn app.main:app --reload
Docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import approvals, projects, regulations, reports, screening
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import get_db, init_db
from app.models import HumanReview, Project, ScreeningRun
from app.schemas import DISCLAIMER
from app.services.jurisdictions import all_jurisdictions
from app.services.regulatory_library import library_stats
from app.services.taxonomy import rules, taxonomy
from app.tools import build_registry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    get_settings().ensure_dirs()
    init_db()   # no-op if Alembic has already created the schema
    yield


app = FastAPI(
    title="EIAGen - Environmental Compliance & EIA Screening Assistant",
    version="0.1.0",
    description=("AI-assisted preliminary environmental screening with retrieval-augmented regulatory search, "
                 "controlled agent workflow, citation verification and mandatory human review.\n\n" + DISCLAIMER),
    lifespan=lifespan,
)
for router in (projects.router, screening.router, reports.router, approvals.router, regulations.router):
    app.include_router(router)


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    db.execute(select(1))
    from app.llm.providers import get_llm_provider

    return {"status": "ok", "version": s.app_version, "database": "ok", "llm_provider": get_llm_provider().name,
            "embedding_backend": s.embedding_backend, "jurisdictions": sorted(all_jurisdictions())}


@app.get("/jurisdictions", tags=["system"])
def jurisdictions() -> dict:
    return all_jurisdictions()


@app.get("/taxonomy", tags=["system"])
def get_taxonomy() -> dict:
    t = taxonomy()
    return {"version": t["taxonomy_version"], "components": t["components"], "sectors": {k: v["label"] for k, v in t["sectors"].items()},
            "activities": {k: v["label"] for k, v in t["activities"].items()},
            "impacts": {k: {"label": v["label"], "component": v["component"]} for k, v in t["impacts"].items()},
            "parameters": rules()["parameters"], "risk_methodology": rules()["risk_methodology"]}


@app.get("/tools", tags=["system"])
def tools() -> list[dict]:
    return build_registry().schemas()


@app.get("/stats", tags=["system"])
def stats(db: Session = Depends(get_db)) -> dict:
    runs = db.scalars(select(ScreeningRun).order_by(ScreeningRun.id.desc()).limit(10)).all()
    projects_ = {p.id: p.name for p in db.scalars(select(Project))}
    return {
        "projects": len(projects_),
        "completed_assessments": db.scalar(select(func.count()).select_from(ScreeningRun).where(ScreeningRun.status == "completed")) or 0,
        "pending_reviews": db.scalar(select(func.count()).select_from(Project).where(
            Project.review_state.in_(["awaiting_human_review", "changes_requested"]))) or 0,
        "reviews_recorded": db.scalar(select(func.count()).select_from(HumanReview)) or 0,
        "knowledge_base": library_stats(db),
        "recent_activity": [{"project_id": r.project_id, "project": projects_.get(r.project_id), "version": r.version,
                             "status": r.status, "review_state": r.review_state, "started_at": r.started_at.isoformat()}
                            for r in runs],
    }
