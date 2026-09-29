-- The two database roles, shared by the test harness (backend/tests/_pg.py) and compose.
-- P0-02 ships the roles only; P0-06 adds schema app, grants and the RLS helpers.
--
-- tumnis_owner owns the schema and runs migrations; tumnis_app is what the api and worker
-- use, never a superuser and never bypassing row-level security. Passwords come from the
-- session settings tumnis.owner_password and tumnis.app_password when the caller sets
-- them (the harness does); unset means no password (peer or certificate auth only).
DO $roles$
DECLARE
  owner_password text := nullif(current_setting('tumnis.owner_password', true), '');
  app_password text := nullif(current_setting('tumnis.app_password', true), '');
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tumnis_owner') THEN
    EXECUTE format(
      'CREATE ROLE tumnis_owner LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOBYPASSRLS PASSWORD %L',
      owner_password);
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tumnis_app') THEN
    EXECUTE format(
      'CREATE ROLE tumnis_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
      app_password);
  END IF;
END
$roles$;
