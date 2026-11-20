"""Text extraction from uploaded documents.

Parsing files from the internet is risky (parser bugs, decompression bombs,
pathological PDFs), so ``extract_isolated`` runs each extraction in a fresh
child process with a wall-clock timeout and, on Linux, a memory cap. The child
is killed if it exceeds either. Nothing in an uploaded file is ever executed.
"""

from __future__ import annotations

import io
import multiprocessing
import re
import sys
import unicodedata
from dataclasses import dataclass

from document_service.filetypes import DOCX, PDF, TXT

PARSER_VERSION = "1.0.0"


class ExtractionError(Exception):
    """``code`` is a short, client-safe reason stored on the document."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExtractedText:
    text: str
    page_count: int | None
    truncated: bool


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Tab, no-break space, the U+2000..U+200B space block, narrow/medium/ideographic spaces.
_SPACE_CHARS = (
    "".join(chr(c) for c in (0x0020, 0x0009, 0x00A0, 0x2000))
    + "-"
    + "".join(chr(c) for c in (0x200B, 0x202F, 0x205F, 0x3000))
)
_SPACES = re.compile(f"[{_SPACE_CHARS}]+")


def normalize_text(text: str) -> str:
    """NFC, no control characters, single spaces, at most one blank line in a row."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_SPACES.sub(" ", _CONTROL.sub("", line)).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _extract_pdf(data: bytes, max_pages: int) -> ExtractedText:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Owner-password-only PDFs open with an empty user password.
            if not reader.decrypt(""):
                raise ExtractionError("encrypted_pdf")
        pages = reader.pages
        count = len(pages)
        parts = [page.extract_text() or "" for page in list(pages)[:max_pages]]
    except ExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ExtractionError("unreadable_file") from exc
    return ExtractedText("\n".join(parts), count, count > max_pages)


def _extract_docx(data: bytes) -> ExtractedText:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises many types for malformed packages
        raise ExtractionError("unreadable_file") from exc
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells: list[str] = []
            for cell in row.cells:
                if cell.text not in cells:  # merged cells repeat their text
                    cells.append(cell.text)
            lines.append(" | ".join(cells))
    return ExtractedText("\n".join(lines), None, False)


def _extract_txt(data: bytes) -> ExtractedText:
    try:
        return ExtractedText(data.decode("utf-8-sig"), None, False)
    except UnicodeDecodeError as exc:
        raise ExtractionError("unreadable_file") from exc


def extract_text(data: bytes, mime_type: str, *, max_pages: int = 50, max_chars: int = 200_000) -> ExtractedText:
    """Extract and normalize text in the current process (use ``extract_isolated``
    for untrusted files)."""
    if mime_type == PDF:
        raw = _extract_pdf(data, max_pages)
    elif mime_type == DOCX:
        raw = _extract_docx(data)
    elif mime_type == TXT:
        raw = _extract_txt(data)
    else:
        raise ExtractionError("unsupported_type")
    text = normalize_text(raw.text)
    if not text:
        # Typically a scanned (image-only) PDF; OCR is not supported yet.
        raise ExtractionError("no_text")
    truncated = raw.truncated or len(text) > max_chars
    return ExtractedText(text[:max_chars], raw.page_count, truncated)


# ------------------------------------------------------------- isolation


def _child(conn, data: bytes, mime_type: str, max_pages: int, max_chars: int, memory_limit_mb: int) -> None:
    if sys.platform.startswith("linux"):
        import resource

        limit = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    try:
        result = extract_text(data, mime_type, max_pages=max_pages, max_chars=max_chars)
        conn.send(("ok", result.text, result.page_count, result.truncated))
    except ExtractionError as exc:
        conn.send(("error", exc.code))
    except MemoryError:
        conn.send(("error", "too_complex"))
    except Exception:
        conn.send(("error", "unreadable_file"))
    finally:
        conn.close()


def extract_isolated(
    data: bytes,
    mime_type: str,
    *,
    timeout: float = 30,
    memory_limit_mb: int = 512,
    max_pages: int = 50,
    max_chars: int = 200_000,
) -> ExtractedText:
    """Run ``extract_text`` in a child process; kill it on timeout. Blocking."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child, args=(child, data, mime_type, max_pages, max_chars, memory_limit_mb), daemon=True
    )
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise ExtractionError("timeout")
        try:
            message = parent.recv()
        except EOFError as exc:  # child died (e.g. killed by the memory cap)
            raise ExtractionError("too_complex") from exc
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent.close()
    if message[0] == "error":
        raise ExtractionError(message[1])
    _, text, page_count, truncated = message
    return ExtractedText(text, page_count, truncated)
