"""Text extraction and chunking with page and section tracking.

PDFs are read with PyMuPDF so that every chunk keeps its page number. Section
headings (Section 12, Regulation 3, Article 4(2), Annex I, Schedule 2, a
paragraph sign, or Markdown headings) are carried forward so each chunk can be
cited as "document, section, page".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageText:
    page: int | None
    text: str


@dataclass
class TextChunk:
    index: int
    text: str
    page: int | None
    section: str | None


# A legal heading is a keyword plus number, optionally followed by a separator or
# a capitalised title ("Section 12 - Power to...", "Regulation 3 Undertakings").
# Body sentences such as "Annex I includes quarries..." are not headings.
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?i:section|regulation|article|part|schedule|annex|anlage|chapter|rule)\s+[\dIVXLC]+[a-z]?(?:\(\w+\))*"
    r"(?:\s*$|\s*[-–—:.]\s*|\s+[A-ZÄÖÜ])"
    r"|§+\s*\d+[a-z]?"
    r"|\d{1,3}\.\s+[A-Z][A-Za-z ,\-]{3,80}$"
    r")"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pages(path: Path) -> list[PageText]:
    """Extract text per page. Non-paginated formats return a single page=None item."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import json

        import pymupdf

        # Scanned PDFs: pages without a text layer are read from an OCR sidecar
        # (<file>.ocr.json, produced by scripts/ocr_pdf.py) so page numbers are kept.
        sidecar = path.with_suffix(path.suffix + ".ocr.json")
        ocr_pages = json.loads(sidecar.read_text(encoding="utf-8"))["pages"] if sidecar.exists() else {}
        pages: list[PageText] = []
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc, start=1):
                text = page.get_text("text")
                if not text.strip() and str(i) in ocr_pages:
                    text = ocr_pages[str(i)]
                pages.append(PageText(page=i, text=text))
        return pages
    if suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                text += "\n" + " | ".join(cell.text for cell in row.cells)
        return [PageText(page=None, text=text)]
    if suffix in (".txt", ".md"):
        return [PageText(page=None, text=path.read_text(encoding="utf-8"))]
    raise ValueError(f"Unsupported document type: {suffix}")


def uses_ocr(path: Path) -> bool:
    return path.with_suffix(path.suffix + ".ocr.json").exists()


def _heading_of(line: str, markdown: bool) -> str | None:
    stripped = line.strip()
    if markdown and stripped.startswith("#"):
        level = len(stripped) - len(stripped.lstrip("#"))
        title = stripped.lstrip("#").strip()
        return title if level >= 2 and title else None
    if markdown:
        return None
    if len(stripped) <= 100 and _HEADING_RE.match(stripped):
        return stripped.rstrip(".:")
    return None


def chunk_pages(pages: list[PageText], chunk_size: int = 900, overlap: int = 150,
                markdown: bool = False) -> list[TextChunk]:
    """Split pages into chunks that never span a section or page boundary."""
    chunks: list[TextChunk] = []
    section: str | None = None

    def flush(buffer: list[str], page: int | None, sec: str | None) -> None:
        text = re.sub(r"[ \t]+", " ", "\n".join(buffer)).strip()
        if len(text) >= 40:
            chunks.append(TextChunk(index=len(chunks), text=text, page=page, section=sec))

    for page in pages:
        buffer: list[str] = []
        size = 0
        for raw in page.text.splitlines():
            line = raw.rstrip()
            if markdown and (line.lstrip().startswith(">") or line.lstrip().startswith("# ")):
                continue  # document banner / title: kept in metadata, not retrieved
            heading = _heading_of(line, markdown)
            if heading:
                flush(buffer, page.page, section)
                buffer, size = [], 0
                section = heading
                if not markdown:
                    buffer, size = [line], len(line)
                continue
            if not line.strip():
                continue
            if size + len(line) > chunk_size and buffer:
                flush(buffer, page.page, section)
                tail = "\n".join(buffer)[-overlap:] if overlap else ""
                buffer, size = ([tail] if tail else []), len(tail)
            buffer.append(line)
            size += len(line) + 1
        flush(buffer, page.page, section)
    return chunks


def detect_markdown(path: Path) -> bool:
    return path.suffix.lower() == ".md"
