"""Fetch and ingest official regulatory texts listed in data/regulations/official_sources.yaml.

    python scripts/fetch_official_sources.py              # fetch missing 'auto' files, OCR if needed, ingest
    python scripts/fetch_official_sources.py --no-download
    python scripts/fetch_official_sources.py --only de-uvpg-official

'manual' sources (publishers that block automated downloads, e.g. EUR-Lex) are
listed with their URL and expected file name: save the PDF there and re-run.
After ingestion each curated summary is linked to its official counterpart, and
summaries of repealed legislation are marked superseded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import init_db, session_scope  # noqa: E402
from app.services.official_sources import ingest_official_sources  # noqa: E402
from app.services.retrieval_service import ensure_index  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--no-ocr", action="store_true", help="skip scanned documents that still need OCR")
    ap.add_argument("--only", help="process a single document_id")
    ap.add_argument("--actor", default="administrator")
    args = ap.parse_args()
    init_db()
    with session_scope() as db:
        rep = ingest_official_sources(db, download_missing=not args.no_download, only=args.only,
                                      actor=args.actor, run_ocr=not args.no_ocr)
        print(f"Ingested: {rep.ingested or '-'}")
        print(f"Already present: {rep.already_present or '-'}")
        for n in rep.notes:
            print(f"Note: {n}")
        for f in rep.failed:
            print(f"FAILED: {f}")
        for did, url, path in rep.missing:
            print(f"MISSING {did}: download from {url}\n          and save as {path}")
        print(f"Vector index: {ensure_index(db).size} active passages")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
