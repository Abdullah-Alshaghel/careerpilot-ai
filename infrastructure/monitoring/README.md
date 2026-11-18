# Monitoring

Local: `docker compose --profile observability up -d` starts `grafana/otel-lgtm` (Grafana on http://127.0.0.1:3001, OTLP/HTTP on :4318). Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4318` in `.env` and restart services to export traces. Dashboards/alerts are added in Phase 9.
