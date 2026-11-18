#!/bin/sh
# Runs once, on first initialisation of the Postgres data volume.
# Each service gets its own database and login role (data ownership,
# architecture §10.1). Services never share credentials or databases.
# Add a line per service as it is introduced.
set -eu

create_service_db() {
  db="$1"; role="$2"; password="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v db="$db" -v role="$role" -v password="$password" <<'EOSQL'
CREATE ROLE :"role" LOGIN PASSWORD :'password';
CREATE DATABASE :"db" OWNER :"role";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"role";
EOSQL
}

# Nobody but the superuser connects to the maintenance databases.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'EOSQL'
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
EOSQL

create_service_db identity identity_svc "$IDENTITY_DB_PASSWORD"
