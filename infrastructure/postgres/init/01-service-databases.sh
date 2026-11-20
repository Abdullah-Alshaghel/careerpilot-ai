#!/bin/sh
# Creates one database and login role per service (data ownership,
# architecture §10.1). Services never share credentials or databases.
#
# Idempotent: runs on first initialisation of the data volume (mounted into
# docker-entrypoint-initdb.d) AND on every `docker compose up` via the
# one-shot `db-provision` service, so services added in later phases get their
# database on existing volumes too. Role passwords are re-synced from .env.
# Add a line per service as it is introduced.
set -eu

PGHOST="${PGHOST:-}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"

run_psql() {
  if [ -n "$PGHOST" ]; then
    psql -v ON_ERROR_STOP=1 --host "$PGHOST" --username "$POSTGRES_USER" --dbname postgres "$@"
  else
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres "$@"
  fi
}

create_service_db() {
  db="$1"; role="$2"; password="$3"
  run_psql -v db="$db" -v role="$role" -v password="$password" <<'EOSQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
ALTER ROLE :"role" WITH LOGIN PASSWORD :'password';
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"role";
EOSQL
}

# Nobody but the superuser connects to the maintenance databases.
run_psql <<'EOSQL'
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
EOSQL

create_service_db identity identity_svc "$IDENTITY_DB_PASSWORD"
create_service_db documents documents_svc "$DOCUMENTS_DB_PASSWORD"
create_service_db career_profile career_profile_svc "$CAREER_PROFILE_DB_PASSWORD"
create_service_db companies companies_svc "$COMPANIES_DB_PASSWORD"
