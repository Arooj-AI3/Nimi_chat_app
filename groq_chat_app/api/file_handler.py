"""
api/file_handler.py

Turns user attachments into something the models can use:

    * Documents (.txt .py .json .csv .md .log .pdf) -> plain text, capped at
      MAX_ATTACHMENT_CHARS so one big PDF cannot blow the provider's
      tokens-per-minute limit.
    * Images (.png .jpg .jpeg .webp .gif .bmp) -> validated, downscaled JPEG copy
      stored in the app folder (sent to the vision model as a base64 data URL).

Every failure is reported through `AttachedFile.error` with a human-readable
message; the UI shows it instead of silently dropping the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import (
    IMAGE_EXTENSIONS,
    MAX_ATTACHMENT_CHARS,
    MAX_ATTACHMENT_SIZE_BYTES,
    SUPPORTED_ATTACHMENT_EXTENSIONS,
)
from api.image_utils import store_upload

try:  # pypdf is the maintained successor of PyPDF2
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None


@dataclass
class AttachedFile:
    """A file attached to a chat message."""

    path: str
    name: str
    extension: str
    size_bytes: int
    content: str = ""              # extracted text (documents)
    image_path: str = ""           # stored, downscaled copy (images)
    truncated: bool = False
    error: Optional[str] = None    # human-readable problem, or None

    @property
    def is_valid(self) -> bool:
        return self.error is None

    @property
    def is_image(self) -> bool:
        return self.extension in IMAGE_EXTENSIONS


def is_supported_extension(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_ATTACHMENT_EXTENSIONS


def extract_file(path: str) -> AttachedFile:
    """Read `path` and return an AttachedFile (with `error` set on failure)."""
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    def fail(message: str, size: int = 0) -> AttachedFile:
        return AttachedFile(path=path, name=name, extension=ext, size_bytes=size, error=message)

    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return fail(str(exc))

    if ext not in SUPPORTED_ATTACHMENT_EXTENSIONS:
        return fail("Unsupported file type.", size)
    if size > MAX_ATTACHMENT_SIZE_BYTES:
        return fail(f"File is too large ({size // (1024 * 1024)} MB, max {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)} MB).", size)
    if size == 0:
        return fail("File is empty.", size)

    try:
        if ext in IMAGE_EXTENSIONS:
            return AttachedFile(path, name, ext, size, image_path=store_upload(path))

        text = _extract_pdf_text(path) if ext == ".pdf" else _extract_plain_text(path)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        return fail(str(exc), size)

    if not text.strip() or _is_blank_pdf_text(text, ext):
        return fail(
            "No readable text found. If this is a scanned PDF, export the pages as images "
            "and attach those instead — the vision model can read them.",
            size,
        )

    truncated = len(text) > MAX_ATTACHMENT_CHARS
    if truncated:
        text = text[:MAX_ATTACHMENT_CHARS] + "\n\n[... truncated to fit the model's limits ...]"
    return AttachedFile(path, name, ext, size, content=text, truncated=truncated)


def _extract_plain_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _extract_pdf_text(path: str) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    reader = PdfReader(path)
    if getattr(reader, "is_encrypted", False):
        try:
            if not reader.decrypt(""):
                raise RuntimeError("This PDF is password-protected.")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("This PDF is password-protected.") from exc
    parts: List[str] = []
    total = 0
    for index, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        parts.append(f"--- Page {index + 1} ---\n{text}")
        total += len(text)
        if total > MAX_ATTACHMENT_CHARS * 2:  # stop early on huge PDFs
            break
    return "\n\n".join(parts).strip()


def _is_blank_pdf_text(text: str, ext: str) -> bool:
    """A scanned PDF yields only the '--- Page N ---' headers and no body."""
    if ext != ".pdf":
        return False
    body = "".join(
        line for line in text.splitlines() if not line.startswith("--- Page ")
    )
    return not body.strip()


# --------------------------------------------------------------------------- #
# Persisted form (what is stored in history.json for each message)
# --------------------------------------------------------------------------- #
def attachments_to_records(attachments: List[AttachedFile]) -> List[Dict]:
    """Keep the extracted text (not the binary) so follow-up questions still work."""
    records: List[Dict] = []
    for a in attachments:
        if not a.is_valid:
            continue
        if a.is_image:
            records.append({"kind": "image", "name": a.name, "image": a.image_path})
        else:
            records.append({"kind": "document", "name": a.name, "text": a.content})
    return records
