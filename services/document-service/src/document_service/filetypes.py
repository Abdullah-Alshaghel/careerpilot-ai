"""Upload validation by content, never by filename or the client's Content-Type.

Accepted: PDF, DOCX (macro-free), UTF-8 plain text. Anything else is refused
before it is stored.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
ACCEPTED_TYPES = (PDF, DOCX, TXT)

# DOCX zip limits: a CV is a few hundred KB uncompressed. These stop zip bombs.
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ZIP_RATIO = 200


class RejectedFile(Exception):
    """The upload is refused. ``code`` is safe to show to the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DetectedType:
    mime_type: str
    extension: str


def _check_docx(data: bytes) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RejectedFile("unsupported_type", "File type not supported") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise RejectedFile("unsafe_file", "File structure is not allowed")
        total = 0
        for info in infos:
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO:
                raise RejectedFile("unsafe_file", "File structure is not allowed")
            if info.filename.startswith("/") or ".." in info.filename.split("/"):
                raise RejectedFile("unsafe_file", "File structure is not allowed")
        if total > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise RejectedFile("unsafe_file", "File structure is not allowed")
        names = {info.filename for info in infos}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise RejectedFile("unsupported_type", "File type not supported")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise RejectedFile("macro_document", "Documents with macros are not accepted")
        content_types = archive.read("[Content_Types].xml")
        if b"macroEnabled" in content_types:
            raise RejectedFile("macro_document", "Documents with macros are not accepted")


def _is_plain_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if not text.strip():
        return False
    controls = sum(1 for ch in text if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t\f")
    return controls / len(text) < 0.01


def detect_type(data: bytes) -> DetectedType:
    """Identify the file from its bytes, or raise RejectedFile."""
    if not data:
        raise RejectedFile("empty_file", "The file is empty")
    if data.startswith(b"%PDF-"):
        return DetectedType(PDF, "pdf")
    if data.startswith(b"PK\x03\x04"):
        _check_docx(data)
        return DetectedType(DOCX, "docx")
    if _is_plain_text(data):
        return DetectedType(TXT, "txt")
    raise RejectedFile("unsupported_type", "Upload a PDF, Word (.docx) or plain-text file")


_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def safe_filename(raw: str | None, detected: DetectedType) -> str:
    """A display name only: never used as a path or object key."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", _UNSAFE_FILENAME_CHARS.sub("_", name)).strip(" ._")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = stem[:200] or "document"
    return f"{stem}.{detected.extension}"
