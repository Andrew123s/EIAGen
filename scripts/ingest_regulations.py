"""Ingest regulatory documents into the knowledge base.

Seed manifest:
    python scripts/ingest_regulations.py --manifest data/regulations/manifest.yaml

A single official document (e.g. a PDF downloaded from the issuing authority):
    python scripts/ingest_regulations.py --file path/to/uvpg.pdf --document-id de-uvpg-official \
        --title "Gesetz ueber die Umweltvertraeglichkeitspruefung (UVPG)" --authority "German Bundestag" \
        --jurisdiction germany --type legislation --version "as amended 2024" \
        --source-url https://www.gesetze-im-internet.de/uvpg/ --supersedes de-uvpg

List the library:
    python scripts/ingest_regulations.py --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import init_db, session_scope  # noqa: E402
from app.models import RegulatoryDocument  # noqa: E402
from app.schemas import ContentForm, DocumentType, RegulationMetadata  # noqa: E402
from app.services.regulatory_library import ingest_file, ingest_manifest, library_stats  # noqa: E402
from app.services.retrieval_service import ensure_index  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--file", type=Path)
    p.add_argument("--document-id")
    p.add_argument("--title")
    p.add_argument("--authority")
    p.add_argument("--jurisdiction")
    p.add_argument("--type", choices=[t.value for t in DocumentType])
    p.add_argument("--content-form", default="official_text", choices=[c.value for c in ContentForm])
    p.add_argument("--version")
    p.add_argument("--source-url")
    p.add_argument("--publication-date")
    p.add_argument("--effective-date")
    p.add_argument("--legal-status")
    p.add_argument("--supersedes")
    p.add_argument("--actor", default="administrator")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    init_db()
    with session_scope() as db:
        if args.manifest:
            docs = ingest_manifest(db, args.manifest, actor=args.actor)
            print(f"Ingested/confirmed {len(docs)} documents from {args.manifest}")
        if args.file:
            missing = [n for n in ("document_id", "title", "authority", "jurisdiction", "type", "version")
                       if not getattr(args, n)]
            if missing:
                p.error("--file requires: " + ", ".join("--" + m.replace("_", "-") for m in missing))
            meta = RegulationMetadata(
                document_id=args.document_id, title=args.title, issuing_authority=args.authority,
                jurisdiction=args.jurisdiction, document_type=args.type, content_form=args.content_form,
                version=args.version, source_url=args.source_url, publication_date=args.publication_date,
                effective_date=args.effective_date, legal_status=args.legal_status, supersedes=args.supersedes)
            doc = ingest_file(db, meta, args.file, actor=args.actor, copy_to_library=True)
            print(f"Ingested {doc.document_id}: {len(doc.chunks)} chunks")
        if args.manifest or args.file:
            db.flush()
            print(f"Vector index: {ensure_index(db).size} active chunks")
        if args.list or not (args.manifest or args.file):
            for d in db.scalars(select(RegulatoryDocument).order_by(RegulatoryDocument.jurisdiction)):
                print(f"{d.jurisdiction:13} {d.document_id:24} {d.status:10} {d.content_form:16} {d.title[:70]}")
            print(library_stats(db)["knowledge_base_version"])


if __name__ == "__main__":
    main()
