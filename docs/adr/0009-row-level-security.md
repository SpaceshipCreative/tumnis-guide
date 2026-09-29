# ADR-0009: Row-level security with a transaction-local workspace setting
Status: Accepted (2026-09-27) | Supersedes: none

## Context
PRD decision 12 makes the data model multi-tenant from the first migration while the product is single-tenant: every tenant row carries `workspace_id`, row-level security is on, and hosting is a deployment mode (Hosted readiness). App connections go through PgBouncer in transaction pooling mode (PERF-1), so any per-connection session state leaks between requests. A test must prove one workspace cannot read, update or delete another's rows through any endpoint or MCP tool.

## Options considered
- **Postgres row-level security with a transaction-local setting.** Each policy compares `workspace_id` with `current_setting('app.workspace_id')`; the setting is set per transaction, so it is safe behind transaction pooling. Isolation does not depend on remembering a `WHERE` clause. Deal-breaker only if the app role can bypass it.
- **Tenant filters in application code only.** Simple. Deal-breaker: one missed `WHERE` leaks data.

## Decision
Every request and every workflow step opens a transaction and runs `set_config('app.workspace_id', <id>, true)` through `tumnis/core/tenancy.py`. Every tenant table is created with `create_tenant_table` and gets a policy on `workspace_id`. The app connects as a role that owns no tables and has no `BYPASSRLS`; migrations run as a separate owner role. `users` is a global identity table reached through SECURITY DEFINER functions.

## Consequences
- The app role must never own tables or bypass RLS; a role test proves it.
- `workspace_id` is the first column of every composite index and unique key.
- Every cache key starts with the workspace ID.
- The isolation suite (`backend/tests/isolation/`) walks every tenant table and every endpoint and MCP tool; the table registry fails when a tenant table lacks a policy.
- Tests that enforce this decision (P0-06):
  - `backend/tumnis/core/tests/integration/test_roles.py` (T-P0-06-05, 06): the app role owns nothing, bypasses nothing and cannot truncate, alter or drop policies.
  - `backend/tests/meta/test_table_registry.py` (T-P0-06-01 to 04): every table in `public` is fenced or on the closed allow-list; `workspace_id` leads every composite index.
  - `backend/tests/isolation/test_rls_tables.py` (T-P0-06-13, 14): per table, workspace B cannot read, change or insert workspace A's rows, and no context sees nothing.
  - `backend/tumnis/core/tests/integration/test_tenancy.py` (T-P0-06-15, 16) and `test_pgbouncer_context.py` (T-P0-06-07, 08): the setting reaches every transaction and cannot leak through PgBouncer.
- The audit log (P0-15) is fenced the same way but append-only: `audit_log` and `audit_anchors` have a SELECT and an INSERT policy and no UPDATE, DELETE or TRUNCATE grant for the app role, and a trigger refuses changes even from the owner. An attacker with the owner password can still disable the trigger and rewrite a whole chain consistently; the nightly anchors, the anchored-head metric (kept outside the database) and the write-no-delete B2 backups are what make that detectable. Tests: `backend/tumnis/core/tests/integration/test_audit_immutability.py` and `test_audit_chain.py`.

## Sources
- [PostgreSQL row security policies](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)
- [PostgreSQL set_config](https://www.postgresql.org/docs/18/functions-admin.html)
- [PgBouncer features and pooling modes](https://www.pgbouncer.org/features.html)
