# HANDOFF: P3-02 (Connections and the sync framework), after c2

Branch `wp/P3-02`; push only with `/usr/bin/git push origin HEAD:wp/P3-02`. **Draft PR #156**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/156). Merge `origin/main` and `origin/wp/P3-02`
first; run `make gen` after any merge that touches routes. Binding: the original prompt, the
coordinator notes (ingest_items PENDING per decision 80; accepted deviations below) and this file.

## State in one paragraph

The backend is DONE and green: every P3-02 backend spec test (T-P3-02-01 to 13) XPASSed in CI run
36962776790 (at 093d0d1f) and its marker is off (commit 883f0622). Remaining: the frontend
(T-P3-02-14, `Connections.test.tsx`, still `test.fails`), one CI run on the current head to confirm,
the PR body, ready + CodeRabbit, the review loop, MERGE-READY.

## Commits (c2)

- `483dfd71` chore: regenerate schemas, P3-02 event fixtures (`items.ingested`, `connection.auth_required`)
- `f654221a` feat: rules (`provider_limit`, `sync_every`, `backfill_start`, `next_sync_at`, `status_after`, `STATUS_TABLE`)
- `4cd9ca91` feat: `FakeSource` (+ `demo_pages`), `FakeOAuthServer` (+ `asgi_app()`), real `adapters/oauth.py`
  `McpOAuthClient` (MCP SDK 1.30.0 helpers over `core.net.guarded_client`), `McpSource` + `TOOL_ALLOWLISTS`,
  `register_adapter("integrations.oauth")`, `register_provider(fake)`, `tests/contract/test_oauth_contract.py`
  (fake + real against the ASGI app), `tests/recordings/fake/*.json`
- `093d0d1f` feat: workflows (`connector_sync`, `connect_oauth`, `connector_sync_tick`, `schedules()`, `use()`),
  `integrations/testing.py` test tick `connector-sync-tick` (router imports it), tasks review kind
  `connection_auth` (in `tasks/review.py`) + subscriber `tasks.connection_auth_review` (in `tasks/events.py`),
  audit cases `connector.disconnected` / `connector.oauth_state_mismatch` in `backend/tests/audit_cases.py`
- `f3ff565a` docs: `docs/plan/p3-02-provider-assumptions.md`, AGENTS.md "New connector checklist", A12 row
- `9654f173` fix: routes tagged `connections` (SDK names `connections*`, were `purges*`); fake OAuth uses
  `hmac.compare_digest` (Semgrep `tumnis-secret-eq`); callback with neither `code` nor `error` -> 404
  `oauth_state_unknown` (A0.3 sweep, see Scott items)
- `883f0622` test: unmark T-P3-02-01..13 (CI run 36962776790, decision 78)
- `bf763449` feat(deploy): `TumnisConnectorSyncStale` alert (+ promtool case; passes locally) and the
  integration conftest imports the adapters package (a lone run found provider `fake` unregistered)
- then: `wip(frontend)` commit of the frontend subagent's files (see below) and this handoff commit

## CI state

Run 36962776790 (093d0d1f): only failures were the expected XPASS(strict) plus three real ones, all
fixed in 9654f173 (not yet confirmed by CI): Semgrep secret-eq in fake_oauth.py; A0.3
`test_workspace_b_cannot_touch_workspace_a[session-GET /v1/connections/oauth/callback]` (400, wants
200/404); frontend `src/lib/live-map.test.ts` T-P0-22-11 (new query ops not in the live map; the
frontend subagent added entries to `frontend/src/lib/live-map.ts`, uncommitted at handoff -> in the
wip commit). Read the next run with `gh pr checks 156`, `gh run view <id> --log-failed` (timeout 120).
`preview` stays pending (expected).

Local evidence (c2): all 9 P3-02 integration tests XPASS locally (Docker, -n 3); the two new audit
cases pass in `tumnis/core/tests/integration/test_audit_actions.py -k connector`; promtool tests pass.
A 20x loop of T-P3-02-05 (kill/resume) was running at handoff
(`/tmp/claude-1002/P3-02-c2/kill20.sh`, output `/tmp/claude-1002/P3-02-c2/kill20.out`; run 1 passed).
The plan's done checklist wants 20 in a row: re-run it (`timeout 3000 bash kill20.sh`, Docker, outside
the sandbox) and quote the result in the PR body.

## Remaining steps (in order)

1. Merge origin/main, `make gen` if routes changed, `make check` (semgrep meta-test fails only in the
   sandbox: read-only `~/.semgrep`; promtool and Docker tests need `dangerouslyDisableSandbox`).
2. Frontend (T-P3-02-14). The c2 frontend subagent's files are in the `wip(frontend)` commit:
   `frontend/src/components/settings/connections/{Connections,ConnectWizard,ConnectionDetail}.tsx`,
   `api.ts`, `status.ts`, `ConnectionsFlows.test.tsx`, `frontend/src/test/msw/connections.ts`, plus edits
   to `components/settings/sections.ts`, `routes/settings.$section.tsx`, `lib/live-map.ts`. Its final
   report arrived after the handoff commit. It says:
   - T-14 body passes: vitest shows "Expect test to fail" (and a throwaway unmarked copy passed, then
     deleted);
   - its 6 `ConnectionsFlows.test.tsx` tests pass;
   - `npm run typecheck` is clean on the final code;
   - full vitest shows 350 passed and 1 "failed" (T-14, "Expect test to fail" only);
   - `live-map.test.ts` T-P0-22-11 passes. The 5 connection ops are in `NOT_LIVE` because there is no
     live entity for connections;
   - lint was clean before the rename and live-map change; after them only `prettier --write` ran.
   It did NOT run `npm run build`, `npm run bundle`, or
   `node --test ../scripts/ci/check_bundle.test.mjs ../perf/thresholds.test.mjs`.
   Context7 it used: `/tanstack/query/v5.90.3` (pinned version is 5.104.0) for refetchInterval,
   enabled/skipToken and useMutation; `/websites/mswjs_io` (pinned version is 3.0.0) for
   request.clone().json() and HttpResponse.
   Open notes:
   - pending_auth shows "Sign in", and disabled rows show no action. Disconnected rows are
     soft-deleted, so they never appear in the list.
   - `e2e/fixtures.ts` SETTINGS_SECTIONS does not include Connections (left alone).
   Still verify it yourself against the spec test
   `Connections.test.tsx` (never edit it); run `cd frontend && npx vitest run src/components/settings/connections --maxWorkers=4`,
   `npm run typecheck`, `npm run lint`, full `npx vitest run --maxWorkers=4`, and the bundle check.
   SDK names are `connections*` (e.g. `connectionsListConnections`). Remove the `test.fails` marker only
   after CI shows T-14 "expected to fail but passed" (cite the run id). Check usable at 375 px. Cite the
   Context7 docs used (React/TanStack Query/MSW/Testing Library at the pinned versions).
3. Push; one background `gh run watch <id>` (timeout 2400) per run.
4. PR body (rewrite fully; old draft at `/tmp/claude-1002/P3-02-c1/pr-body.md`): summary; per-layer
   results; shared-file edits (below); deviations with sources (below); Context7/first-party citations
   (below); `ingest_items` / `POST /ingest` PENDING (decision 80); Scott items. End with a blank line and
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. `gh pr edit 156 --body-file ...`.
5. `gh pr ready 156`, then once: `gh pr comment 156 --body "@coderabbitai review"`. Review loop per
   `~/tumnis-coordinator/pr-review-loop.md`.
6. When CI is green and no open CodeRabbit threads: SendMessage to main "#156 MERGE-READY at <sha>".
7. Delete HANDOFF.md in a chore commit.

## Shared-file edits (for the PR body)

`backend/tumnis/core/metrics.py` (2 gauges), `core/ratelimit.py` (`SlidingWindows`), `backend/tests/fixtures/__init__.py`
(`Fakes.oauth_server`, `build_app`), `backend/tests/meta/test_security_definer.py` (allow `app.connector_sync_ages`),
`backend/tests/audit_cases.py` (2 cases), `backend/tumnis/modules/tasks/review.py` + `events.py` (kind + subscriber),
`deploy/prometheus/alerts.yml` + `alerts.test.yml`, `AGENTS.md`, `docs/IMPLEMENTATION-PLAN-DETAILED.md` (A12 row),
generated `schemas/`, `frontend/src/api/*`, `backend/tests/contract/generated/test_schema_events.py`.
Migrations: `integrations_0003` (expand), `integrations_0004` (contract; drops `uq_sync_state_ws_connection`).

## Deviations (accepted by the coordinator; list each with its source)

- Dedup ID `sync:<id>` with `duplication_policy="return-existing"` instead of a queue partition key: DBOS
  3.1.0 refuses `queue_partition_key` with `deduplication_id` (`dbos/_queue.py:625`); return-existing is
  not allowed in `enqueue_in_transaction` (`dbos/_client.py:298`), so the api enqueues outside a transaction.
  Docs: Context7 `/dbos-inc/dbos-docs` queue tutorial ("Singleton workflows", "Deduplication").
- Sliding-window limiter (`core.ratelimit.SlidingWindows`) instead of a token bucket: a bucket's burst can
  overrun a documented per-minute limit (T-P3-02-07 asserts no 60 s window over 10).
- One fetch-and-persist step per page (a raw page is never a DBOS step output; resume refetches, T-05).
- Token refresh under the connection row lock (`SELECT ... FOR UPDATE` in `api.access_token`), T-04.
- MCP SDK pinned 1.30.0 (httpx; `streamable_http_client(url, http_client=)` yields a 3-tuple; helpers in
  `mcp.client.auth.utils`), while Context7 `/websites/py_sdk_modelcontextprotocol_io` shows v2 (httpx2,
  2-tuple). Code follows the installed 1.30.0 source.
- New in c2: callback without `code`/`error` answers 404 (see Scott items).
- New in c2: `TOOL_ALLOWLISTS` narrows per source (`allowed_tools` intersected with the built-in list,
  never widened).

## Citations (PR body)

- MCP authorization spec (OAuth 2.1 + PKCE, RFC 9728, RFC 8414, RFC 7591, RFC 8707), RFC 6749 s5.2/s6,
  RFC 7636, RFC 9207; MCP SDK 1.30.0 source `mcp/client/auth/utils.py`, `mcp/client/streamable_http.py`;
  Context7 `/websites/py_sdk_modelcontextprotocol_io` (transports, ClientSession).
- DBOS 3.1.0 source (`_queue.py`, `_client.py`, `_core.py` return-existing with a parent) and Context7
  `/dbos-inc/dbos-docs` queue tutorial.
- Prometheus `promtool test rules` docs (already linked in alerts.test.yml).

## Scott items

- `ingest_items` / `POST /ingest` stays PENDING (decision 80 defers P3-01 and the connectors).
- `account` placeholder = the connection id until a provider reports the account identity (limits are
  per connection meanwhile; assumption 7 in the assumptions doc).
- Locked-test conflict, built around: A0.3's sweep sends `GET /v1/connections/oauth/callback?state=x`
  (no code) and expects 200/404, while T-P3-02-12 wants a stray callback (with a code) to be 400
  `oauth_state_mismatch`. c2 answers 404 `oauth_state_unknown` (not audited) when neither `code` nor
  `error` is present (RFC 6749 s4.1.2: not an authorization response). Options for Scott: keep this; or
  exempt the callback from the A0.3 sweep (spec change); or make every unknown state 404 (changes T-12).
  Message main about it (not yet sent).
- `TumnisConnectorSyncStale` threshold 2 h for 10 min is a plan-default guess; Scott may tune it.
- `McpOAuthClient` defaults to the hosted net policy (public addresses only) because the registry builds
  it with no settings; a self-hosted MCP server on the LAN would need the deployment policy passed in.

## Verify

```bash
cd backend && uv run pytest -q -n 3 -m "not integration" tumnis/modules/integrations
cd backend && uv run pytest -q -n 3 tumnis/modules/integrations/tests/integration   # Docker, outside the sandbox
make check
cd frontend && npx vitest run src/components/settings/connections --maxWorkers=4
```
