"""Test fixtures: every test runs against its own temporary database and data directory,
with offline hashing embeddings and (unless a test injects a mock) no LLM."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import reset_settings  # noqa: E402
from app.database import get_session_factory, init_db, reset_engine  # noqa: E402
from app.llm.providers import set_provider_override  # noqa: E402
from app.services.embedding_service import reset_embedding_service  # noqa: E402
from app.services.retrieval_service import reset_index_cache  # noqa: E402

MINERAL_DESCRIPTION = (
    "A company proposes to establish a mineral processing facility near a river. The project will involve land "
    "clearing, excavation, water abstraction, ore processing, wastewater generation, and transportation. The site "
    "covers 45 hectares and will employ 250 workers. Water demand is 1,200 m3/day.")


def _reset() -> None:
    reset_settings()
    reset_engine()
    reset_embedding_service()
    reset_index_cache()
    set_provider_override(None)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EIAGEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIAGEN_EMBEDDING_BACKEND", "hashing")
    monkeypatch.setenv("EIAGEN_LLM_PROVIDER", "rule_based")
    for var in ("EIAGEN_DATABASE_URL", "EIAGEN_API_KEY", "EIAGEN_ADMIN_API_KEY", "EIAGEN_REPORTS_DIR"):
        monkeypatch.delenv(var, raising=False)
    _reset()
    init_db()
    yield tmp_path
    _reset()


@pytest.fixture
def db():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def kb(db):
    from app.services.regulatory_library import ingest_manifest

    ingest_manifest(db)
    return db


@pytest.fixture
def client(kb):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_llm():
    from app.llm.providers import MockProvider

    provider = MockProvider()
    set_provider_override(provider)
    yield provider
    set_provider_override(None)


def make_project(db, **overrides):
    from app.models import Project

    data = dict(name="Test Mineral Plant", developer="Test Ltd", jurisdiction="ghana", region="Western",
                location="Near Tarkwa", description=MINERAL_DESCRIPTION, characteristics={},
                environmental_setting="The site is 300 m from the river; villages lie within 2 km.")
    data.update(overrides)
    project = Project(**data)
    db.add(project)
    db.flush()
    return project


def make_pdf(path: Path, pages: list[str]) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


PROJECT_PAYLOAD = {
    "name": "API Test Plant",
    "developer": "Test Ltd",
    "description": MINERAL_DESCRIPTION,
    "sector": "mining",
    "jurisdiction": "ghana",
    "region": "Western",
    "location": "Near Tarkwa",
    "environmental_setting": "300 m from the river; villages within 2 km.",
}
