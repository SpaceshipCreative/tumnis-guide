-- Per-database setup (P0-06, ADR-0009), run as the superuser on `tumnis` and on every test
-- template (backend/tests/_pg.py drops the \connect line and runs it on the template).
-- Compose runs initdb .sql files against the maintenance database, hence the \connect.
\connect tumnis

-- Only the app role (and the owner, which owns the database) may connect.
DO $grants$
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO tumnis_app', current_database());
END
$grants$;

-- Tables live in public (R-01), owned by the owner role; the app role may use, not create.
ALTER SCHEMA public OWNER TO tumnis_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO tumnis_app;

-- Schema app holds the tenancy helpers and the SECURITY DEFINER functions.
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION tumnis_owner;
GRANT USAGE ON SCHEMA app TO tumnis_app;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

-- Every table the owner creates is readable and writable by the app role, nothing more:
-- no TRUNCATE, REFERENCES or TRIGGER. Narrower tables (the audit log, P0-15) revoke in
-- their own migration. Functions are private until a migration grants EXECUTE.
ALTER DEFAULT PRIVILEGES FOR ROLE tumnis_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tumnis_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tumnis_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO tumnis_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tumnis_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
