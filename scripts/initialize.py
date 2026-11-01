"""Initialise EIAGen: create directories, apply database migrations, ingest the seed knowledge base.

    python scripts/initialize.py            # migrate + ingest seed manifest + build vector index
    python scripts/initialize.py --no-ingest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database import session_scope  # noqa: E402
from app.services.regulatory_library import ingest_manifest, library_stats  # noqa: E402
from app.services.retrieval_service import ensure_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ingest", action="store_true", help="Skip seed knowledge-base ingestion")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    from sqlalchemy import inspect

    from app.database import get_engine

    tables = set(inspect(get_engine()).get_table_names())
    if "projects" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")   # schema created earlier by the API's create_all
    else:
        command.upgrade(cfg, "head")
    print(f"Database ready: {settings.database_url}")

    if not args.no_ingest:
        with session_scope() as db:
            docs = ingest_manifest(db)
            print(f"Seed knowledge base: {len(docs)} documents")
            store = ensure_index(db)
            print(f"Vector index: {store.size} chunks ({settings.embedding_backend})")
            stats = library_stats(db)
            print(f"Knowledge-base version: {stats['knowledge_base_version']}")
            print(f"Active documents by jurisdiction: {stats['active_by_jurisdiction']}")


if __name__ == "__main__":
    main()
