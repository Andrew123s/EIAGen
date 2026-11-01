"""Access control, upload validation and prompt-injection safeguards.

Uploaded project documents and retrieved regulatory text are untrusted. They are
never executed and never interpreted as instructions: the deterministic pipeline
only extracts facts from them, and any text passed to an LLM is wrapped in
delimiters with instruction-like sentences removed and flagged.
"""

from __future__ import annotations

import hmac
import re
import uuid
from pathlib import Path

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

# ----------------------------------------------------------------- API keys


def _matches(provided: str | None, expected: str | None) -> bool:
    return bool(provided) and bool(expected) and hmac.compare_digest(provided, expected)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Write access. Open in local single-user mode when no key is configured."""
    settings = get_settings()
    if settings.api_key is None and settings.admin_api_key is None:
        return
    if _matches(x_api_key, settings.api_key) or _matches(x_api_key, settings.admin_api_key):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key header")


def require_admin_key(x_api_key: str | None = Header(default=None)) -> None:
    """Regulatory-library changes require the admin key when one is configured."""
    settings = get_settings()
    if settings.admin_api_key is None:
        return require_api_key(x_api_key)
    if _matches(x_api_key, settings.admin_api_key):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator key required for regulatory library changes")


# ------------------------------------------------------------ File uploads

_MAGIC = {
    ".pdf": [b"%PDF-"],
    ".docx": [b"PK\x03\x04"],
}


class UploadValidationError(ValueError):
    pass


def safe_filename(original: str) -> str:
    """Return a server-side file name that cannot escape the upload directory."""
    stem = Path(original).name
    suffix = Path(stem).suffix.lower()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(stem).stem)[:60] or "file"
    return f"{uuid.uuid4().hex[:12]}_{cleaned}{suffix}"


def validate_upload(filename: str, content: bytes) -> str:
    """Validate extension, size and file signature. Returns the lower-case extension."""
    settings = get_settings()
    if not filename:
        raise UploadValidationError("File name is required")
    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_extensions:
        raise UploadValidationError(
            f"File type '{ext or 'none'}' not allowed; allowed: {', '.join(settings.allowed_extensions)}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) == 0:
        raise UploadValidationError("File is empty")
    if len(content) > max_bytes:
        raise UploadValidationError(f"File exceeds the {settings.max_upload_mb} MB upload limit")
    signatures = _MAGIC.get(ext)
    if signatures and not any(content.startswith(sig) for sig in signatures):
        raise UploadValidationError(f"File content does not match the '{ext}' format")
    if ext in (".txt", ".md"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadValidationError("Text files must be UTF-8 encoded") from exc
    return ext


# --------------------------------------------------- Prompt-injection guard

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts|rules)",
    r"disregard\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|system)",
    r"forget\s+(all\s+|your\s+)?(previous\s+)?instructions",
    r"you\s+are\s+now\s+",
    r"new\s+instructions?\s*:",
    r"system\s+prompt",
    r"act\s+as\s+(an?\s+)?(administrator|admin|system|developer)",
    r"(declare|mark|state|certify|classify)\s+(this|the)\s+project\s+(as\s+)?"
    r"(exempt|approved|compliant|fully\s+compliant)",
    r"(approve|issue)\s+(the|this)\s+(project|permit|report|assessment)\s+(automatically|now|immediately)",
    r"no\s+(eia|environmental\s+impact\s+assessment)\s+is\s+required",
    r"(execute|run)\s+(the\s+following\s+)?(code|command|script|shell)",
    r"call\s+the\s+\w+\s+tool",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def detect_injection(text: str) -> list[str]:
    """Return sentences that look like instructions aimed at an AI system."""
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip() and _INJECTION_RE.search(s)]


def neutralise_injection(text: str) -> tuple[str, list[str]]:
    """Remove instruction-like sentences. Returns (clean_text, removed_sentences)."""
    flagged = detect_injection(text)
    if not flagged:
        return text, []
    parts = [s for s in _SENTENCE_SPLIT.split(text) if s.strip() and not _INJECTION_RE.search(s)]
    return " ".join(p.strip() for p in parts), flagged


def wrap_untrusted(label: str, text: str) -> str:
    """Delimit untrusted content for an LLM prompt (after neutralisation)."""
    clean, _ = neutralise_injection(text)
    clean = clean.replace("</untrusted_content>", "")
    return (f'<untrusted_content source="{label}">\n{clean}\n</untrusted_content>')
