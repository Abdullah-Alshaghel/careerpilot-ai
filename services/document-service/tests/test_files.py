from __future__ import annotations

import io
import zipfile

import docx
import pytest
from fpdf import FPDF
from pypdf import PdfWriter

from document_service.extraction import ExtractionError, extract_isolated, extract_text, normalize_text
from document_service.filetypes import DOCX, PDF, TXT, RejectedFile, detect_type, safe_filename


def make_pdf(*lines: str, pages: int = 1) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for _ in range(pages):
        pdf.add_page()
        for line in lines:
            pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def make_docx(*paragraphs: str, table: list[list[str]] | None = None) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        t = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def rezip(data: bytes, extra: dict[str, bytes]) -> bytes:
    """Copy a zip and add entries."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        for name, content in extra.items():
            dst.writestr(name, content)
    return out.getvalue()


# ----------------------------------------------------------- type sniffing


def test_detects_by_content_not_name():
    assert detect_type(make_pdf("Ada")).mime_type == PDF
    assert detect_type(make_docx("Ada")).mime_type == DOCX
    assert detect_type(b"Ada Lovelace\nEngineer").mime_type == TXT
    assert detect_type("\ufeffسيرة ذاتية".encode()).mime_type == TXT


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "unsupported_type"),  # Windows executable
        (b"\x89PNG\r\n\x1a\n\x00\x00", "unsupported_type"),
        (b"PK\x03\x04garbage", "unsupported_type"),
        ("caf\u00e9".encode("latin-1"), "unsupported_type"),  # not UTF-8
        (b"text with \x00 nul", "unsupported_type"),
    ],
)
def test_rejects_other_content(data, code):
    with pytest.raises(RejectedFile) as err:
        detect_type(data)
    assert err.value.code == code


def test_rejects_generic_zip_that_is_not_a_word_document():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(RejectedFile, match="not supported"):
        detect_type(out.getvalue())


def test_rejects_macro_enabled_word_documents():
    with pytest.raises(RejectedFile) as err:
        detect_type(rezip(make_docx("Ada"), {"word/vbaProject.bin": b"\x00" * 10}))
    assert err.value.code == "macro_document"


def test_rejects_zip_bombs():
    bomb = rezip(make_docx("Ada"), {"word/media/huge.bin": b"\x00" * (20 * 1024 * 1024)})
    with pytest.raises(RejectedFile) as err:
        detect_type(bomb)
    assert err.value.code == "unsafe_file"


def test_rejects_path_traversal_entries():
    with pytest.raises(RejectedFile) as err:
        detect_type(rezip(make_docx("Ada"), {"../../evil.sh": b"echo"}))
    assert err.value.code == "unsafe_file"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CV_2026.pdf", "CV_2026.pdf"),
        ("C:\\Users\\me\\My CV.PDF", "My CV.pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ("evil<script>.pdf", "evil_script_.pdf"),
        ("", "document.pdf"),
        (None, "document.pdf"),
        ("report.exe", "report.pdf"),  # extension always follows the real type
        ("\u0633\u064a\u0631\u0629.pdf", "\u0633\u064a\u0631\u0629.pdf"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw, detect_type(make_pdf("x"))) == expected


# ------------------------------------------------------------- extraction


def test_extracts_pdf_text_and_page_count():
    result = extract_text(make_pdf("Ada Lovelace", "Analytical Engine", pages=2), PDF)
    assert "Ada Lovelace" in result.text
    assert result.page_count == 2
    assert not result.truncated


def test_pdf_page_cap_marks_truncation():
    result = extract_text(make_pdf("Page text", pages=3), PDF, max_pages=2)
    assert result.page_count == 3
    assert result.truncated
    assert result.text.count("Page text") == 2


def test_extracts_docx_paragraphs_and_tables():
    data = make_docx("Ada Lovelace", "Skills", table=[["Python", "SQL"], ["Rust", "Go"]])
    text = extract_text(data, DOCX).text
    assert "Ada Lovelace" in text
    assert "Python | SQL" in text


def test_extracts_arabic_text_unchanged():
    arabic = "\u0627\u0644\u0645\u0647\u0627\u0631\u0627\u062a: \u0628\u0627\u064a\u062b\u0648\u0646"
    assert extract_text(arabic.encode(), TXT).text == arabic
    assert arabic in extract_text(make_docx(arabic), DOCX).text


def test_encrypted_pdf_is_reported():
    writer = PdfWriter()
    writer.append(io.BytesIO(make_pdf("secret")))
    writer.encrypt(user_password="pw", owner_password="owner")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(ExtractionError) as err:
        extract_text(out.getvalue(), PDF)
    assert err.value.code == "encrypted_pdf"


def test_image_only_pdf_has_no_text():
    with pytest.raises(ExtractionError) as err:
        extract_text(make_pdf(), PDF)  # a page with no text at all
    assert err.value.code == "no_text"


def test_corrupt_pdf_is_unreadable():
    with pytest.raises(ExtractionError) as err:
        extract_text(b"%PDF-1.7\n garbage garbage", PDF)
    assert err.value.code in {"unreadable_file", "no_text"}


def test_long_text_is_truncated():
    result = extract_text(("word " * 5000).encode(), TXT, max_chars=1000)
    assert len(result.text) == 1000
    assert result.truncated


def test_normalize_text():
    assert normalize_text("  a \t b \r\n\r\n\r\n\n c\x00 ") == "a b\n\nc"


def test_isolated_extraction_round_trip():
    result = extract_isolated(make_pdf("Isolated Ada"), PDF, timeout=60)
    assert "Isolated Ada" in result.text


def test_isolated_extraction_reports_errors_from_the_child():
    with pytest.raises(ExtractionError) as err:
        extract_isolated(make_pdf(), PDF, timeout=60)
    assert err.value.code == "no_text"


def test_isolated_extraction_times_out():
    with pytest.raises(ExtractionError) as err:
        # Far below the cost of starting a Python child process.
        extract_isolated(make_pdf("slow"), PDF, timeout=0.001)
    assert err.value.code == "timeout"
