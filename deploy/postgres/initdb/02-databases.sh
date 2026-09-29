#!/bin/sh
# Compose only (the test harness runs 01-roles.sql itself): role passwords from the
# environment, then the tumnis database (owned by tumnis_owner, which runs migrations) and
# the DBOS system database tumnis_dbos (owned by tumnis_app, the role DBOS runs as).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$OWNER_DB_PASSWORD" -v app_pw="$APP_DB_PASSWORD" <<'SQL'
ALTER ROLE tumnis_owner PASSWORD :'owner_pw';
ALTER ROLE tumnis_app PASSWORD :'app_pw';
CREATE DATABASE tumnis OWNER tumnis_owner;
CREATE DATABASE tumnis_dbos OWNER tumnis_app;
SQL
