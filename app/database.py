"""Database engine and session management (SQLAlchemy 2.0).

SQLite is used locally; the models avoid SQLite-specific types so that the
``EIAGEN_DATABASE_URL`` can point at PostgreSQL later.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args = {}
        if url.startswith("sqlite"):
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            connect_args = {"check_same_thread": False}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Dispose the engine so a new database URL takes effect (tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    """Create tables directly (tests, first run). Production uses Alembic."""
    import app.models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=get_engine())


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
