"""Download (where permitted) and ingest the official texts listed in official_sources.yaml."""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import RegulatoryDocument
from app.schemas import DocumentStatus, RegulationMetadata
from app.services.regulatory_library import DocumentConflict, ingest_file, set_status, update_metadata

log = get_logger("official_sources")
USER_AGENT = "EIAGen regulatory ingestion (+https://github.com/Andrew123s/EIAGen)"


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    missing: list[tuple[str, str, str]] = field(default_factory=list)   # (document_id, url, expected path)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def load_sources() -> dict:
    path = get_settings().regulations_dir / "official_sources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"sources": []}


def official_folder() -> Path:
    return get_settings().regulations_dir / "official"


def download(url: str, dest: Path) -> str | None:
    """Plain download; returns an error message instead of retrying or working around blocks."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if not data.startswith(b"%PDF-"):
        return "response was not a PDF (publisher may block automated access)"
    dest.write_bytes(data)
    return None


def ingest_official_sources(db: Session, *, download_missing: bool = False, only: str | None = None,
                            actor: str = "administrator", run_ocr: bool = False) -> IngestReport:
    manifest = load_sources()
    folder = official_folder()
    folder.mkdir(parents=True, exist_ok=True)
    report = IngestReport()
    for src in manifest.get("sources", []):
        did = src["document_id"]
        if only and did != only:
            continue
        path = folder / src["file"]
        if not path.exists() and download_missing and src.get("fetch") == "auto":
            err = download(src["url"], path)
            if err:
                report.notes.append(f"{did}: download failed - {err}")
        if not path.exists():
            report.missing.append((did, src["url"], str(path)))
            continue
        if src.get("observed_sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != src["observed_sha256"]:
            report.notes.append(f"{did}: file differs from the version observed on {manifest.get('observed_on')}")
        if src.get("ocr_required") and not path.with_suffix(".pdf.ocr.json").exists():
            if not run_ocr:
                report.notes.append(f"{did}: scanned PDF needs OCR (python scripts/ocr_pdf.py {path})")
                report.missing.append((did, src["url"], str(path)))
                continue
            from scripts.ocr_pdf import ocr_pdf  # heavy optional dependency

            ocr_pdf(path)
        existed = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == did)) is not None
        try:
            ingest_file(db, RegulationMetadata(document_id=did, **src["metadata"]), path, actor=actor)
        except DocumentConflict as exc:
            report.failed.append(f"{did}: {exc}")
            continue
        (report.already_present if existed else report.ingested).append(did)
        linked = src.get("summary_document_id")
        if linked:
            s = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == linked))
            if s is not None and s.official_counterpart != did:
                update_metadata(db, linked, {"official_counterpart": did}, actor)
        for old in src.get("supersedes_summaries", []):
            s = db.scalar(select(RegulatoryDocument).where(RegulatoryDocument.document_id == old))
            if s is not None and s.status == "active":
                set_status(db, old, DocumentStatus.superseded, actor, superseded_by=did,
                           comment=f"Repealed/replaced by {src['metadata']['title']}")
    db.commit()
    return report
