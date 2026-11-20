"""CV upload → extraction → suggested facts → review, through the real gateway,
services, workers, RabbitMQ and S3. Critical tests #2 to #4 and #15.

Run with CAREERPILOT_INTEGRATION=1.
"""

from __future__ import annotations

import io
import os
import time
import uuid
import zipfile

import boto3
import docx
import httpx
import pytest
import redis
from botocore.config import Config
from fpdf import FPDF
from pypdf import PdfWriter

from tests.integration.conftest import GATEWAY, env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

PASSWORD = "correct horse battery staple"
CV_LINES = [
    "Grace Hopper",
    "grace@example.com | +1 202 555 0147",
    "Work Experience",
    "Senior Software Engineer, Remington Rand Inc.    Jun 2019 - Present",
    "- Built the first compiler",
    "Education",
    "Doctor of Philosophy (PhD) in Mathematics",
    "Yale University, 1930 - 1934",
    "Skills",
    "COBOL, FLOW-MATIC, Leadership",
]


def reset_rate_limits() -> None:
    """These tests register many accounts from one IP; the register limit is
    tested elsewhere (test_auth_stack), so clear its counters here."""
    client = redis.Redis(host="127.0.0.1", port=6379, password=env("REDIS_PASSWORD"))
    for key in client.scan_iter("rl:*"):
        client.delete(key)
    client.close()


def make_docx(lines: list[str]) -> bytes:
    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class User:
    def __init__(self) -> None:
        reset_rate_limits()
        self.http = httpx.Client(base_url=GATEWAY, timeout=30, headers={"x-client-type": "web"})
        tokens = self.http.post(
            "/v1/auth/register", json={"email": f"docs-{uuid.uuid4().hex[:10]}@example.com", "password": PASSWORD}
        ).json()
        self.id = tokens["user"]["id"]
        self.http.headers["authorization"] = f"Bearer {tokens['access_token']}"

    def upload(self, data: bytes, filename: str = "cv.docx") -> httpx.Response:
        return self.http.post("/v1/documents", files={"file": (filename, data, "application/octet-stream")})

    def wait_for_document(self, document_id: str, timeout: float = 45) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            document = self.http.get(f"/v1/documents/{document_id}").json()
            if document["status"] in ("extracted", "failed"):
                return document
            time.sleep(0.5)
        raise AssertionError(f"document still {document['status']}")

    def facts(self, predicate=lambda f: True, *, at_least: int = 1, timeout: float = 30) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            facts = [f for f in self.http.get("/v1/profile").json()["facts"] if predicate(f)]
            if len(facts) >= at_least or time.monotonic() > deadline:
                return facts
            time.sleep(0.5)


@pytest.fixture
def alice() -> User:
    return User()


def s3_keys(prefix: str) -> list[str]:
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:8333",
        region_name="us-east-1",
        aws_access_key_id=env("S3_ACCESS_KEY"),
        aws_secret_access_key=env("S3_SECRET_KEY"),
        config=Config(s3={"addressing_style": "path"}),
    )
    listing = client.list_objects_v2(Bucket="careerpilot-documents", Prefix=prefix)
    return [item["Key"] for item in listing.get("Contents", [])]


# ------------------------------------------------------------- happy path


def test_word_cv_becomes_suggested_facts_for_review(alice):
    upload = alice.upload(make_docx(CV_LINES), "Grace CV (final).docx")
    assert upload.status_code == 201
    document = upload.json()
    assert document["filename"] == "Grace CV (final).docx"
    assert document["status"] in ("uploaded", "processing", "extracted")

    assert alice.wait_for_document(document["id"])["status"] == "extracted"
    facts = alice.facts(at_least=8)
    assert all(f["verification_status"] == "extracted" for f in facts)
    assert all(f["source"]["document_id"] == document["id"] for f in facts)
    assert all(f["source"]["name"] == "Grace CV (final).docx" for f in facts)
    job = next(f for f in facts if f["fact_type"] == "employment")
    assert job["value"]["title"] == "Senior Software Engineer"
    assert job["value"]["organization"] == "Remington Rand Inc"
    assert job["value"]["current"] is True
    assert {f["value"]["name"] for f in facts if f["fact_type"] == "skill"} == {"COBOL", "FLOW-MATIC", "Leadership"}

    # Review: approve one, reject one, fix one, add one.
    skill = next(f for f in facts if f["fact_type"] == "skill")
    email = next(f for f in facts if f["value"].get("field") == "email")
    assert (
        alice.http.patch(f"/v1/profile/facts/{skill['id']}", json={"verification_status": "user_verified"}).status_code
        == 200
    )
    assert (
        alice.http.patch(f"/v1/profile/facts/{email['id']}", json={"verification_status": "rejected"}).status_code
        == 200
    )
    fixed = alice.http.patch(
        f"/v1/profile/facts/{job['id']}", json={"value": {**job["value"], "title": "Rear Admiral"}}
    )
    assert fixed.json()["value"]["title"] == "Rear Admiral"
    assert fixed.json()["verification_status"] == "user_verified"
    added = alice.http.post("/v1/profile/facts", json={"fact_type": "language", "value": {"name": "English"}})
    assert added.status_code == 201

    profile = alice.http.get("/v1/profile").json()
    statuses = {f["id"]: f["verification_status"] for f in profile["facts"]}
    assert statuses[skill["id"]] == "user_verified"
    assert statuses[email["id"]] == "rejected"


def test_pdf_cv_is_extracted(alice):
    document = alice.upload(make_pdf(CV_LINES), "cv.pdf").json()
    assert document["mime_type"] == "application/pdf"
    assert alice.wait_for_document(document["id"])["status"] == "extracted"
    assert alice.facts(lambda f: f["fact_type"] == "skill", at_least=3)


def test_same_file_twice_returns_the_existing_document(alice):
    data = make_docx(CV_LINES)
    first, second = alice.upload(data), alice.upload(data, "renamed.docx")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(alice.http.get("/v1/documents").json()["documents"]) == 1


def test_storage_keys_are_per_user_and_never_contain_the_filename(alice):
    document = alice.upload(make_docx(CV_LINES), "secret-name.docx").json()
    alice.wait_for_document(document["id"])
    keys = s3_keys(f"users/{alice.id}/documents/{document['id']}/")
    assert f"users/{alice.id}/documents/{document['id']}/original" in keys
    assert any(k.endswith(".txt") for k in keys)  # extracted text stored beside it
    assert not any("secret-name" in k for k in keys)


# ---------------------------------------------------------- hostile input


@pytest.mark.parametrize(
    ("data", "filename", "status", "code"),
    [
        (b"MZ\x90\x00" + b"\x00" * 100, "cv.pdf", 415, "unsupported_type"),  # executable named .pdf
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "cv.png", 415, "unsupported_type"),
        (b"", "empty.txt", 422, "empty_file"),
    ],
)
def test_disguised_and_empty_files_are_refused(alice, data, filename, status, code):
    resp = alice.upload(data, filename)
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_macro_enabled_word_file_is_refused(alice):
    source = make_docx(CV_LINES)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("word/vbaProject.bin", b"\x00" * 64)
    resp = alice.upload(out.getvalue(), "cv.docx")
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "macro_document"


def test_files_over_10_mb_are_refused(alice):
    resp = alice.upload(b"a" * (10 * 1024 * 1024 + 1), "big.txt")
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


def test_encrypted_pdf_fails_with_a_clear_reason(alice):
    writer = PdfWriter()
    writer.append(io.BytesIO(make_pdf(CV_LINES)))
    writer.encrypt(user_password="pw", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    document = alice.upload(buffer.getvalue(), "locked.pdf").json()
    finished = alice.wait_for_document(document["id"])
    assert (finished["status"], finished["failure_reason"]) == ("failed", "encrypted_pdf")


# ------------------------------------------------------------- isolation


def test_documents_and_facts_are_private_to_their_owner(alice):
    document = alice.upload(make_docx(CV_LINES)).json()
    alice.wait_for_document(document["id"])
    fact = alice.facts()[0]

    bob = User()
    for method, path in (
        ("GET", f"/v1/documents/{document['id']}"),
        ("DELETE", f"/v1/documents/{document['id']}"),
        ("DELETE", f"/v1/profile/facts/{fact['id']}"),
    ):
        resp = bob.http.request(method, path)
        assert resp.status_code == 404, (method, path)
    patch = bob.http.patch(f"/v1/profile/facts/{fact['id']}", json={"verification_status": "rejected"})
    assert patch.status_code == 404
    assert bob.http.get("/v1/documents").json()["documents"] == []
    assert bob.http.get("/v1/profile").json()["facts"] == []
    # Alice's data is untouched.
    assert alice.http.get(f"/v1/documents/{document['id']}").status_code == 200


def test_document_routes_require_a_token():
    with httpx.Client(base_url=GATEWAY, timeout=10) as anonymous:
        for method, path in (("GET", "/v1/documents"), ("POST", "/v1/documents"), ("GET", "/v1/profile")):
            assert anonymous.request(method, path).status_code == 401


# --------------------------------------------------------------- delete


def test_deleting_a_document_removes_the_file_and_its_unreviewed_suggestions(alice):
    document = alice.upload(make_docx(CV_LINES)).json()
    alice.wait_for_document(document["id"])
    facts = alice.facts(at_least=8)
    kept = next(f for f in facts if f["fact_type"] == "skill")
    alice.http.patch(f"/v1/profile/facts/{kept['id']}", json={"verification_status": "user_verified"})

    assert alice.http.delete(f"/v1/documents/{document['id']}").status_code == 204
    assert alice.http.get(f"/v1/documents/{document['id']}").status_code == 404
    assert s3_keys(f"users/{alice.id}/documents/{document['id']}/") == []

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        remaining = alice.http.get("/v1/profile").json()["facts"]
        if len(remaining) == 1:
            break
        time.sleep(0.5)
    assert [f["id"] for f in remaining] == [kept["id"]]  # the approved fact stays
