# HANDOFF: P3-02 (Connections and the sync framework)

Branch `wp/P3-02` (local branch renamed from the throwaway; push with
`/usr/bin/git push origin HEAD:wp/P3-02`). Base: main at 21be459. No PR yet. No CI yet.
Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-02.txt` (read it first, plus the
files it lists). Scott decision 71: build provider-agnostic against fakes; record every
provider assumption in `docs/plan/p3-02-provider-assumptions.md` (not written yet).

## State

Committed in the handoff commit (WIP, not yet the formal red commit):

- `backend/tumnis/modules/integrations/tests/unit/test_rules.py`: appended T-P3-02-06, T-P3-02-08 (strict xfail; verified XFAIL).
- `backend/tumnis/modules/integrations/tests/unit/test_mcp_client.py`: T-P3-02-13 (verified XFAIL).
- `backend/tumnis/modules/integrations/tests/integration/test_connections.py`: T-01, T-02, T-12.
- `backend/tumnis/modules/integrations/tests/integration/test_connect_oauth.py`: T-03, T-04.
- `backend/tumnis/modules/integrations/tests/integration/test_connector_sync.py`: T-05, T-07, T-09, T-10.
- `backend/tumnis/modules/integrations/tests/contract/test_connector_template.py`: T-11.
- Helpers (not locked): `tests/integration/_connections.py`, `tests/integration/_fake_pages.py`
  (kill-test subprocess import), conftest fixture `oauth_server` (uses `fakes.oauth_server`).

NOT done:
1. `ruff check` still flags 5 PT018 (compound asserts) in the integration test files; split them
   before the red commit. Then `make check`, then amend into a proper
   `test(integrations): P3-02 spec tests (red)` commit (or a follow-up commit).
2. Red not yet confirmed for integration/contract tests. CAUTION: the `oauth_server` fixture
   resolves `fakes.oauth_server` at SETUP, so until `Fakes.oauth_server` (add a property in
   `backend/tests/fixtures/__init__.py` returning `self["integrations.oauth"]`, shared file) and
   the adapter exist, T-01/02/03/04/12 ERROR instead of XFAIL. Add the property + a minimal
   registered fake first, or accept errors only locally before implementing.
3. Frontend spec T-P3-02-14 `frontend/src/components/settings/connections/Connections.test.tsx`
   (`test.fails('[P3-02][FR-14.4] shows status, last sync and reconnect', ...)`): not written.
   Use a dynamic import of the component inside the test so the file loads before it exists.
4. All implementation (below), docs, AGENTS.md checklist, Part A row, PR, review loop.

## Design (settled with the advisor; follow it)

Verified facts:
- DBOS 3.1.0 refuses `queue_partition_key` + `deduplication_id` together
  (`dbos/_queue.py:625`; docs: dbos-docs queue-tutorial "partitioned queues ... cannot be combined
  with deduplication_id"). The `sync` queue is shared unpartitioned by calendar, knowledge
  folder_sync and coolify. So: NO partition key; dedup `sync:<connection_id>` with
  `SetEnqueueOptions(deduplication_id=..., duplication_policy="return-existing")`; per-provider
  limits enforced by a sliding-window limiter in `core/ratelimit.py` (plan risk note: core.ratelimit
  is the source of truth). Deviation, cite the source.
- MCP SDK pinned 1.30.0 uses `httpx` + `ClientSession`, `streamable_http_client(url, http_client=...)`,
  `mcp.client.auth.oauth2.TokenStorage` (4 methods), helpers in `mcp.client.auth.utils`
  (`build_protected_resource_metadata_discovery_urls`, `build_oauth_authorization_server_metadata_discovery_urls`,
  `handle_protected_resource_response`, `handle_auth_metadata_response`, `create_client_registration_request`,
  `handle_registration_response`), `PKCEParameters.generate()`; models in `mcp.shared.auth`
  (`OAuthToken`, `OAuthClientMetadata`, `OAuthClientInformationFull`). Context7 shows v2 docs
  (httpx2, `Client`) — follow 1.30.0, cite both in the PR.
- `tasks.api` imports `integrations.api`, so integrations cannot import tasks (acyclic graph).
- DBOSClient.send to an unknown workflow raises; `/v1/connections/oauth/callback` must be declared
  before `/v1/connections/{connection_id}`.
- `integrations` router is unprefixed (`v1_router("integrations", tags=...)`), so `/v1/connections` works.
- integrations alembic head: `integrations_0002`.

Interfaces the spec tests already import (implement exactly):
- `integrations.rules`: `ConnectionStatus` (StrEnum: pending_auth, ok, syncing, degraded,
  auth_required, disabled), `SyncOutcome` (StrEnum: success, transient_error, auth_error),
  `ConnectionSettings` (backfill_days=30, sync_every_min=None, allowlist=[], extra={}),
  `ProviderLimit(requests, period_s)`, `PROVIDER_LIMITS` (fake 10/60, granola 90/60,
  inbox_zero 60/60, slack 45/60, google_drive 300/60), `DEFAULT_SYNC_MIN` (fake 5, inbox_zero 5,
  granola 30, chat 5, google_drive 10, s3 15), `backfill_start(now, settings, cap)`,
  `next_sync_at(now, last, every_min, failures, jitter_s=0)` (failures=0: (last or now)+every;
  else now+min(60, every*2**failures) min + jitter), `status_after(prev, outcome)` (table in T-08).
  Re-export these from `api.py`.
- `integrations.api`: `create_connection(ctx, provider, settings, *, account_label=...)` -> `ConnectionOut`
  (status pending_auth; `account` placeholder = connection id string, an assumption),
  `get_connection`, `set_connection_status(ctx, id, status)` (keep accepting calendar's legacy
  "needs_reauth"/"error"; map them to auth_required/degraded on read), `set_next_sync_at(ctx, id, at|None)`
  (None = due now), `ConnectionTokenStorage(ctx, connection_id, *, clock)` (SDK TokenStorage over
  `credentials_enc`; blob `{"tokens": {..., "expires_at"}, "client_info": {...}, "server": {...}}`,
  sealed with aad `connections:<id>`), `store_oauth_server(ctx, id, server)`, `oauth_server_of(ctx, id)`,
  `access_token(ctx, id, *, oauth, clock)` (SELECT ... FOR UPDATE on the connection row, refresh if
  expiring, persist rotated tokens in the same transaction), `fetch_page(ctx, id, cursor, *, connector,
  clock, sleep)` (rate-limited by `provider:<name>:<account>`), `ReauthRequired(provider, message)`,
  models `ConnectionOut`, `ConnectionCreate`, `ConnectionPatch`, `ConnectionSettings` (OpenAPI names
  asserted in T-02; no credential fields).
- `integrations.workflows`: `use(oauth=, sources=, clock=, sleep=)` returning a dict of the previous
  values (the helper calls `workflows.use(**previous)`); workflows named `integrations_connector_sync`
  (args `(workspace_id: str, connection_id: str)`, returns `{"status": "ok"|...}`),
  `connector_sync_tick(scheduled_at, context)`, `connect_oauth`; killpoint
  `integrations.sync.page_<n>.persisting` inside the page step before commit; `schedules()` with
  `connector-sync-tick` `* * * * *` on queue `sync`; `register_tick("connector-sync-tick", ...)` for
  `POST /v1/test/tick/connector-sync-tick` (R-37).
- One step per page that FETCHES AND PERSISTS (calendar's pattern): no raw provider page is ever a
  DBOS step output (it would sit unencrypted in the system DB), and on resume page 3 is refetched,
  which is what T-05 asserts. Deviation from the plan's two-step sketch.
- Initial cursor seeded by the framework: `{"schema_version": 1, "scope": s, "since": iso}` with
  `since = backfill_start(...)` first time, later `last_success_at`; connector's `next_cursor` kept
  when `has_more` is False as the resume token. Multi-scope via optional `connector.scopes()`
  (`getattr`), default `["default"]`. Don't add required members to `Connector`; keep `SyncPage`
  (`deleted`, `has_more`, dict cursor) unchanged.
- `adapters/fake_source.py`: `FakeSource(ScriptedConnector)` provider `fake`, stateless by cursor
  (`cursor["page"]` index), `DEFAULT_PAGES` (module list), `demo_pages(n, per)` (external ids
  `msg-p<page>-<n>`, 1-based), `script_errors(*exc)` queue, `fail_on`, `clock=`, `started` (clock time
  per fetch), `calls`. Register `register_connector("fake", "email", real=FakeSource, fake=FakeSource)`
  plus provider metadata (auth "oauth", server_url `https://mcp.fake.example/mcp`, hidden in real mode).
  Recordings folder `tests/recordings/fake/*.json` (scripted format) for T-11.
- `adapters/fake_oauth.py`: `FakeOAuthServer` implementing the OAuth port (`discover`, `register`,
  `exchange`, `refresh`) + test hooks: `authorization_endpoint`, `clients`, `calls` (op tuples:
  discover, register, exchange, refresh), `refreshes`, `approve(authorize_url, code)` (records the
  PKCE challenge), `issue(client_id, expires_in)`, `refresh_token_live(t)`, rotating refresh tokens,
  `asgi_app()` for the real adapter's contract test. Token strings must not look like credentials
  (`fake-access-1`, `fake-refresh-1`). Register adapter `integrations.oauth` (real = SDK-helper
  client over `core.net.guarded_client`, fake = FakeOAuthServer) with a contract suite (fake + real
  against the ASGI app) — the adapter meta-test requires both.
- `adapters/mcp_client.py`: `McpSource(provider, server_url, allowed_tools, token, policy, transport=None)`,
  `call(tool, args)` raises `ToolNotAllowed(tool)` before any I/O; `TOOL_ALLOWLISTS` per provider
  (no `send*` tools).
- Migrations: `integrations_0003` expand (connections: account_label, settings jsonb default '{}',
  status_detail, last_success_at, next_sync_at, consent_ack_at, failures int default 0; sync_state:
  scope text not null default 'default', page_no int default 0, unique (workspace_id, connection_id,
  scope); oauth_pending: connection_id, workflow_id, iss) and, if squawk rejects DROP INDEX in expand,
  `integrations_0004` contract dropping `uq_sync_state_ws_connection`. Switch `save_sync_cursor`'s
  ON CONFLICT to the new index (calendar keeps working).
- Review item: integrations emits `connection.auth_required` (new event payload in `payloads.py`,
  fixture `backend/tests/contract/fixtures/events/connection.auth_required/v1.json`, `make gen`);
  tasks registers kind `connection_auth` (owner_module "integrations", actions accept+snooze) and
  subscriber `tasks.connection_auth_review` (dedupe_key `conn_auth:<id>`, target type `connection`).
  Also `items.ingested{connection_id, item_ids}` per committed page.
- Audit: `connector.connected` recorded in the worker exchange step with the starting user's actor
  carried through the workflow args; `connector.disconnected` (DELETE with JSON `{reason}`, 204;
  soft-delete, credentials cleared, status disabled, records kept) and `connector.oauth_state_mismatch`
  (callback, 400, audited in its own committed transaction) in routes. Add AuditCases for the two
  route actions to `backend/tests/audit_cases.py`; note why `connected` has none (worker-recorded).
- Metrics: `SECURITY DEFINER app.connector_sync_ages()` (add to `tests/meta/test_security_definer.py`
  ALLOWED with a reason — data-only), `export_metrics(conn)` in integrations api, SnapshotGauges
  `tumnis_connector_sync_age_seconds{provider}` (now() - coalesce(last_success_at, created_at), max per
  provider) and `tumnis_connector_items_total{provider}` (sum of sync_state.items_seen) in core/metrics.py.
- Routes (session only): GET/POST `/v1/connections`, GET/PATCH/DELETE `/v1/connections/{id}`,
  POST `/{id}/oauth/start` (202 `{workflow_id}`), GET `/{id}/oauth/url` (`{authorize_url|null}` via
  DBOSClient.get_event), GET `/v1/connections/oauth/callback` (302 to
  `/settings/connections?connection=<id>`), POST `/{id}/sync` (202), optionally GET
  `/v1/connections/providers`.
- `ingest_items` / `POST /ingest`: leave pending (A10 names P3-02 but the section's scope/tests don't;
  it is the P3-01 fallback needing Scott's sign-off). Scott item in the PR body.
- Frontend: `components/settings/connections/*` (list with status chips, last sync, Reconnect on
  auth_required, Sync now, Disconnect; connect wizard; detail with settings), `connections` in
  `sections.ts`, SCREENS and the loader switch in `routes/settings.$section.tsx`, MSW handlers; 375 px.
- Docs: `docs/plan/p3-02-provider-assumptions.md` (every provider constant above, cursor/since,
  account placeholder, DCR vs CIMD, rotating refresh, redirect URI, Granola 30-day cap, consent
  notice, tool allow-lists), AGENTS.md "new connector" checklist, Part A A12 row for P3-02.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/integrations
cd backend && uv run pytest -q -n 3 tumnis/modules/integrations   # Docker: use bare `make test-int` or CI
make check
```
