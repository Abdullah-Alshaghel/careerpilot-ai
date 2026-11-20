# Events

Envelope: `shared/event-schemas/envelope.v1.schema.json` (Python model: `careerpilot_common.events.EventEnvelope`; contract-tested both ways). Payload schemas: `shared/event-schemas/payloads/` (generated from the producers' models by `scripts/export-contracts.py`).

| Field | Purpose |
|---|---|
| `event_id` | UUID; consumers' idempotency key (processed IDs stored per consumer) |
| `event_type` / `schema_version` | Routing key `<event_type>.v<schema_version>` |
| `occurred_at` | UTC, timezone-aware |
| `correlation_id` | Same ID as the originating HTTP request (consumers log with it) |
| `aggregate_id`, `user_id` | Entity and owner; consumers act only for that owner |
| `producer` | Emitting service |

## Implemented (Phase 3)

| Event | Producer | Consumers | Payload |
|---|---|---|---|
| `document.text_extracted.v1` | document-service (worker) | career-profile worker | `document_id`, `filename`, `mime_type`, `parser_version`, `document_text` (≤ 200 000 chars), `truncated` |
| `document.deleted.v1` | document-service (API) | career-profile worker | `document_id` |

`document_text` carries CV content across the internal broker (ADR-018). It is never logged: `document_text` is a redacted log key, tested in `tests/contract`.

Registered for later phases: `search.started`, `job.discovered`, `job.analyzed`, `job.matched`, `approval.requested`, `job.approved`, `job.rejected`, `application.package.generated`, `application.approved`, `application.submitted`, `application.status_changed`, `notification.requested`.

## Topology (`careerpilot_common.messaging`)

- Exchange `careerpilot.events` (topic, durable); vhost `careerpilot`.
- One durable queue per consumer (`<service>.<purpose>`, e.g. `career-profile.document-events`) with `x-dead-letter-exchange: careerpilot.dlx`.
- Failure handling: a failing message is redelivered once, then dead-lettered to `<queue>.dlq`. Malformed or unknown events go straight to the DLQ. (Delayed retry queues are a later refinement.)
- **Outbox pattern:** producers write the event row (`outbox_events`) in the same DB transaction as the state change; a relay publishes pending rows in order with publisher confirms and marks them sent (`FOR UPDATE SKIP LOCKED`, so replicas don't double-publish). A broker outage delays events but never loses them.
- Consumers are idempotent on `event_id` (`processed_events`), so redelivery never duplicates side effects (critical test #16, `tests/integration/test_profile_and_outbox.py`).
