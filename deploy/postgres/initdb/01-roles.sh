#!/bin/sh
# The two database roles and the two databases (P0-06, ADR-0009). Compose runs this once,
# on first start, as the superuser; the test harness (backend/tests/_pg.py) runs the SQL
# below with its own passwords, so both build the same roles.
#
# tumnis_owner owns the schema and runs migrations. tumnis_app is what the api and worker
# use: never a superuser, never bypassing row-level security, owning nothing, inheriting
# nothing. tumnis_dbos is the DBOS system database, owned by the app role DBOS runs as.
# Passwords come from the environment and reach psql as variables, never in the SQL text.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$OWNER_DB_PASSWORD" -v app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE tumnis_owner LOGIN PASSWORD :'owner_password'
  NOSUPERUSER NOCREATEROLE CREATEDB NOBYPASSRLS NOREPLICATION;
CREATE ROLE tumnis_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS NOREPLICATION NOINHERIT;
CREATE DATABASE tumnis OWNER tumnis_owner;
CREATE DATABASE tumnis_dbos OWNER tumnis_app;
SQL
