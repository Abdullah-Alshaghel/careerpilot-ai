# Build context: repository root (see docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 app

COPY shared/python /src/shared/python
COPY services/identity-service /src/service
RUN pip install /src/shared/python[postgres,rabbitmq,telemetry,auth] /src/service

USER app
WORKDIR /src/service
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"
# Migrations run as a separate one-shot container (identity-migrate), not here,
# so scaled replicas never race on schema changes.
CMD ["uvicorn", "--factory", "identity_service.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
