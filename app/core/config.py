"""Application configuration.

Settings come from ``config/settings.yaml`` and are overridden by ``EIAGEN_*``
environment variables (optionally loaded from a local ``.env`` file). Domain
configuration (jurisdictions, impact taxonomy, assessment rules) is loaded from
the YAML files in ``config/`` so that it can be extended without code changes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Settings(BaseModel):
    app_name: str = "EIAGen"
    app_version: str = "0.1.0"

    data_dir: Path
    regulations_dir: Path
    uploads_dir: Path
    processed_dir: Path
    vector_store_dir: Path
    geospatial_dir: Path
    reports_dir: Path

    database_url: str

    llm_provider: str = "rule_based"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    openai_base_url: str = "http://localhost:11434/v1"
    openai_model: str = "llama3.1:8b"
    llm_timeout_seconds: int = 120

    embedding_backend: str = "sentence_transformers"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150
    top_k: int = 6
    hybrid: bool = True
    min_relevance: float = 0.15

    max_upload_mb: int = 20
    allowed_extensions: list[str] = Field(default_factory=lambda: [".pdf", ".txt", ".md", ".docx"])
    reserved_reviewer_names: list[str] = Field(default_factory=list)

    api_key: str | None = None
    admin_api_key: str | None = None

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.uploads_dir, self.processed_dir,
                  self.vector_store_dir, self.reports_dir):
            p.mkdir(parents=True, exist_ok=True)


def _resolve(path: str | Path, base: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (base / p)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")
    raw = yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))
    env = os.environ.get

    paths = raw["paths"]
    # EIAGEN_DATA_DIR relocates all runtime data (used by tests for isolation).
    data_dir = _resolve(env("EIAGEN_DATA_DIR", paths["data_dir"]), PROJECT_ROOT)
    data_root_overridden = "EIAGEN_DATA_DIR" in os.environ

    def data_path(key: str, sub: str) -> Path:
        if data_root_overridden:
            return data_dir / sub
        return _resolve(paths[key], PROJECT_ROOT)

    regulations_dir = _resolve(env("EIAGEN_REGULATIONS_DIR", paths["regulations_dir"]), PROJECT_ROOT)
    reports_dir = _resolve(env("EIAGEN_REPORTS_DIR", str(data_dir / "reports") if data_root_overridden
                               else paths["reports_dir"]), PROJECT_ROOT)
    default_db = f"sqlite:///{(data_dir / 'eiagen.db').as_posix()}"

    llm, retrieval = raw["llm"], raw["retrieval"]
    return Settings(
        app_name=raw["app"]["name"],
        app_version=raw["app"]["version"],
        data_dir=data_dir,
        regulations_dir=regulations_dir,
        uploads_dir=data_path("uploads_dir", "uploads"),
        processed_dir=data_path("processed_dir", "processed"),
        vector_store_dir=data_path("vector_store_dir", "vector_store"),
        geospatial_dir=_resolve(paths["geospatial_dir"], PROJECT_ROOT),
        reports_dir=reports_dir,
        database_url=env("EIAGEN_DATABASE_URL") or default_db,
        llm_provider=env("EIAGEN_LLM_PROVIDER", llm["provider"]),
        anthropic_model=env("EIAGEN_ANTHROPIC_MODEL", llm["anthropic_model"]),
        anthropic_effort=env("EIAGEN_ANTHROPIC_EFFORT", llm["anthropic_effort"]),
        openai_base_url=env("EIAGEN_OPENAI_BASE_URL", llm["openai_base_url"]),
        openai_model=env("EIAGEN_OPENAI_MODEL", llm["openai_model"]),
        llm_timeout_seconds=int(llm.get("timeout_seconds", 120)),
        embedding_backend=env("EIAGEN_EMBEDDING_BACKEND", retrieval["embedding_backend"]),
        embedding_model=env("EIAGEN_EMBEDDING_MODEL", retrieval["embedding_model"]),
        chunk_size_chars=retrieval["chunk_size_chars"],
        chunk_overlap_chars=retrieval["chunk_overlap_chars"],
        top_k=retrieval["top_k"],
        hybrid=retrieval["hybrid"],
        min_relevance=retrieval["min_relevance"],
        max_upload_mb=int(env("EIAGEN_MAX_UPLOAD_MB", raw["uploads"]["max_upload_mb"])),
        allowed_extensions=raw["uploads"]["allowed_extensions"],
        reserved_reviewer_names=raw["security"]["reserved_reviewer_names"],
        api_key=env("EIAGEN_API_KEY") or None,
        admin_api_key=env("EIAGEN_ADMIN_API_KEY") or None,
    )


def reset_settings() -> None:
    """Clear cached settings and configuration (used by tests)."""
    get_settings.cache_clear()
    load_config.cache_clear()


@lru_cache(maxsize=16)
def load_config(name: str) -> dict[str, Any]:
    """Load a YAML file from ``config/`` (e.g. ``impact_taxonomy``)."""
    path = CONFIG_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
