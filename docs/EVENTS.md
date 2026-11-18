# Events

Envelope: `shared/event-schemas/envelope.v1.schema.json` (Python model: `careerpilot_common.events.EventEnvelope`; contract-tested both ways).

| Field | Purpose |
|---|---|
| `event_id` | UUID; consumers' idempotency key (processed IDs stored per consumer) |
| `event_type` / `schema_version` | Routing key `<event_type>.v<schema_version>` |
| `occurred_at` | UTC, timezone-aware |
| `correlation_id` | Same ID as the originating HTTP request |
| `aggregate_id`, `user_id` | Entity and owner; consumers verify ownership |
| `producer` | Emitting service |

Registered types: `search.started`, `job.discovered`, `job.analyzed`, `job.matched`, `approval.requested`, `job.approved`, `job.rejected`, `application.package.generated`, `application.approved`, `application.submitted`, `application.status_changed`, `notification.requested`.

## Topology (implemented with the first producer, Phase 5)

- Exchange `careerpilot.events` (topic, durable); vhost `careerpilot`.
- One durable queue per consumer (`<service>.<purpose>`) with `x-dead-letter-exchange: careerpilot.dlx`.
- Retries: bounded, with delay (per-queue retry queues with TTL); then the DLQ `<queue>.dlq`.
- **Outbox pattern:** producers write the event row in the same DB transaction as the state change; a relay publishes it with publisher confirms and marks it sent.
- Consumers are idempotent (`event_id` uniqueness), so redelivery never duplicates side effects. Critical test #16 is added with this.
