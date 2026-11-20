# document-service

**Status:** implemented in Phase 3.

**Responsibility:** secure upload of CVs and other documents (PDF, DOCX, UTF-8 text), content-based type checks, isolated text extraction, storage in private S3 object storage.

**Owns data:** `documents`, `document_extractions`, `outbox_events` (database `documents`, role `documents_svc`). No other service reads these tables or the bucket; others use the API below or the events.

## API (through the gateway, bearer token required)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/documents` | multipart field `file`; `201` new, `200` same bytes already uploaded; `413 file_too_large` (> 10 MB), `415 unsupported_type` / `macro_document`, `422 empty_file`, `409 document_limit` (50) |
| `GET` | `/v1/documents` | the caller's documents, newest first |
| `GET` | `/v1/documents/{id}` | `404` for anything not owned by the caller |
| `DELETE` | `/v1/documents/{id}` | removes metadata and stored objects; emits `document.deleted` |

Status flow: `uploaded` → `processing` → `extracted` or `failed` (`failure_reason`: `encrypted_pdf`, `no_text`, `unreadable_file`, `timeout`, `too_complex`, `processing_failed`).

## Processes

- **API** (`uvicorn --factory document_service.main:create_app`).
- **Worker** (`python -m document_service.worker`): claims uploaded documents with `FOR UPDATE SKIP LOCKED`, extracts text in a child process (30 s timeout, 512 MB memory cap on Linux), stores the text in S3, records the extraction and a `document.text_extracted` event in one transaction, and relays the outbox to RabbitMQ. A worker crash leaves the document `processing`; it is retried after 5 minutes, up to 3 attempts.

## Safety rules

- File type is detected from the bytes, never from the filename or `Content-Type`. Executables, images, macro-enabled Word files, zip bombs and path-traversal archive entries are refused before anything is stored.
- Object keys are `users/{user_id}/documents/{document_id}/...`; the uploaded filename is display-only.
- Files are never executed, rendered or served back.
- Logs never contain document text; `document_text` is a redacted log key.
