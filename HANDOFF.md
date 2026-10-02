# HANDOFF: P3-02 (Connections and the sync framework), after c1

Branch `wp/P3-02`; push only with `/usr/bin/git push origin HEAD:wp/P3-02`. **Draft PR #156**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/156); body draft at
`/tmp/claude-1002/P3-02-c1/pr-body.md` (rewrite it fully before marking ready). Base: main at
21be459; origin/main has since moved to 6ec80f3 (#139, P2-06: agents_0010, agent_surface
PENDING_TOOLS changed). **Merge origin/main next, then `make gen`** (coordinator instruction).
Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-02.txt` plus this file. CI runs only
on PRs; decision 78 needs a CI run id that shows a test passing (XPASS) before its marker comes
off. Request `@coderabbitai review` once, when marking the PR ready (coordinator). P3-12, P3-13
and P3-09 wait on this merge, so prioritise the framework, then the frontend and docs.

## Commits

- `4fdc8124` chore: P3-02 handoff (c0: the spec tests, WIP)
- `93cbd2cf` test(integrations): P3-02 spec tests (red) with the framework interfaces (c1)
- this handoff commit

`make check` at 93cbd2cf: all green except `tests/meta/test_security_job.py::test_semgrep_rules_pass_their_own_tests`,
which fails only in the agent sandbox (semgrep cannot write `~/.semgrep`, read-only FS). It is
environmental; CI runs it. 3 unit xfails (T-06, T-08, T-13) confirmed XFAIL. Integration tests
not yet run in c1 (Docker: use CI, or one bare `make test-int` per continuation).

## What exists (committed in 93cbd2cf)

- Spec tests (do not edit assertions):
  - `tests/unit/test_rules.py` (T-06, T-08)
  - `tests/unit/test_mcp_client.py` (T-13)
  - `tests/integration/test_connections.py` (T-01, T-02, T-12)
  - `tests/integration/test_connect_oauth.py` (T-03, T-04)
  - `tests/integration/test_connector_sync.py` (T-05, T-07, T-09, T-10)
  - `tests/contract/test_connector_template.py` (T-11)
  - `frontend/src/components/settings/connections/Connections.test.tsx` (T-14, `test.fails`)

  c1 changed only the form of three asserts: it split the PT018 compound asserts and changed
  one `f.input.args` to `f.input["args"]`. The meaning is unchanged.
- Helpers:
  - `tests/integration/_connections.py`: `metrics_app` now uses the new `tests.fixtures.build_app`, because import-linter forbids module tests importing `tumnis.app`.
  - `_fake_pages.py`
  - conftest `oauth_server`
  - `Fakes.oauth_server` property (shared `tests/fixtures/__init__.py`)
- `rules.py`: enums, `ConnectionSettings`, `ProviderLimit`, `PROVIDER_LIMITS`, `DEFAULT_SYNC_MIN`, `FALLBACK_SYNC_MIN=15`,
  `MAX_BACKOFF_MIN=60`, `STATUS_TABLE` (empty). The functions are **STUBS** (`NotImplementedError`):
  - `provider_limit`: `PROVIDER_LIMITS.get(p, DEFAULT_LIMIT)`.
  - `sync_every`: `settings.sync_every_min or DEFAULT_SYNC_MIN.get(p, 15)`.
  - `backfill_start`: `now - min(backfill_days, cap)` days.
  - `next_sync_at`:
    - failures == 0: `(last or now) + every`;
    - else: `now + min(60, every*2**failures)` min + `jitter_s`.
  - `status_after`:
    - disabled stays disabled;
    - success → ok;
    - auth_error → auth_required;
    - transient_error → degraded from ok, syncing or degraded; otherwise unchanged.

  Check the T-08 table in `test_rules.py`.
- Migrations: `integrations_0003` (expand, squawk-clean) and `integrations_0004` (contract, drops
  `uq_sync_state_ws_connection`). `app.connector_sync_ages()` is SECURITY DEFINER and listed in the
  ALLOWED list of `tests/meta/test_security_definer.py`.
- `models.py`: new columns. `payloads.py`/`events.py`: `ItemsIngestedV1`,
  `ConnectionAuthRequiredV1`. **Event schema fixtures not yet generated**: add
  `backend/tests/contract/fixtures/events/{items.ingested,connection.auth_required}/v1.json` if needed,
  then `make gen`.
- `core/metrics.py`: `CONNECTOR_SYNC_AGE` and `CONNECTOR_ITEMS` SnapshotGauges.
  `core/ratelimit.py`: `SlidingWindows.admit(key, limit, period_s, now) -> wait_s | None`.
- `oauth_port.py`: `OAuthServer`, `OAuthRefused`, the `OAuthPort` protocol, `ADAPTER="integrations.oauth"`.
  The adapter is **not yet registered**. Register it with real and fake, plus a contract suite with
  fake and real classes (the adapter meta-test requires both).
- `api.py`: implemented (not stubs). See the section between "# --- Connections (P3-02)" and
  "# --- Artifacts", and the "P3-02" workflow constants: `SYNC_QUEUE`, `SYNC_WORKFLOW`,
  `CONNECT_WORKFLOW`, `TICK_WORKFLOW`, `AUTHORIZE_URL_EVENT`, `OAUTH_TOPIC`, `CALLBACK_PATH` and
  `sync_dedup_id`. Not yet exercised by tests, so expect bugs when the integration tests first run.
- `router.py`: all `/v1/connections` routes (providers, list, create 201, callback 302/400 before
  `/{id}`, get, patch, delete 204 with `{reason}`, `oauth/start` 202, `oauth/url`, `sync` 202).
  The meta-tests pass (route registry, CSRF and so on).
- `adapters/__init__.py` registers connector `fake` (FakeSource) next to `scripted`. `FakeSource`
  (`adapters/fake_source.py`), `FakeOAuthServer` (`adapters/fake_oauth.py`), `McpSource` and
  `TOOL_ALLOWLISTS` (`adapters/mcp_client.py`) are **STUBS**. `register_provider(ProviderSpec("fake",
  "email", "Fake mail", server_url="https://mcp.fake.example/mcp", fake_only=True))` is not yet called.
- `workflows.py`: `use`, `connector_sync` and `connector_sync_tick` are **STUBS**.

## Remaining steps (in order)

1. Merge `origin/main` (`/usr/bin/git merge origin/main`), run `make gen`, then `make check`, then push.
2. Implement `rules.py`. Remove the T-06 and T-08 markers only after CI shows XPASS (cite the run id).
3. Implement the adapters per the design below: FakeSource, FakeOAuthServer (with `asgi_app()`),
   the real `adapters/oauth.py` (SDK helpers in `mcp.client.auth.utils` over
   `core.net.guarded_client`), `register_adapter("integrations.oauth", ...)`, a contract test
   (`tests/contract/test_oauth_contract.py`, fake and real against the ASGI app), `McpSource` with
   allow-lists (no `send*`), `tests/recordings/fake/*.json`, and `register_provider` for fake.
   T-13 and T-11 should then go green.
4. Workflows (module `workflows.py`):
   - `use(oauth=, sources=, clock=, sleep=)` returns the previous values.
   - `connector_sync`, registered as `integrations_connector_sync`:
     1. begin step (`api.begin_sync`);
     2. one step per page (`api.fetch_page` + `api.persist_page` in one `tenant_session`, with
        `faults.killpoint(f"integrations.sync.page_{n}.persisting")` before commit). Catch
        `AdapterUnavailable` and `ReauthRequired` in the step and return an outcome, so DBOS does
        not retry them;
     3. finish step (`api.finish_sync`).

     It returns `{"status": ...}`.
   - `connect_oauth` (`integrations_connect_oauth`, args ws, conn, redirect_uri, actor):
     1. `prepare_oauth` step;
     2. `DBOS.set_event(AUTHORIZE_URL_EVENT, url)`;
     3. `DBOS.recv(OAUTH_TOPIC, timeout 900)`;
     4. `complete_oauth` step, or `expire_oauth` on timeout.
   - `connector_sync_tick` (`integrations_connector_sync_tick`):
     - For each workspace, call `due_connections`.
     - Skip connections that already have an ENQUEUED or PENDING `integrations_connector_sync`;
       check `input["args"][1]` through `DBOS.list_workflows`, which is safe in a workflow.
     - Enqueue the rest with `SetEnqueueOptions(deduplication_id=sync_dedup_id(id),
       duplication_policy="return-existing")`.
   - `schedules()`: `connector-sync-tick` `* * * * *` on queue `sync`.
   - `register_tick("connector-sync-tick", ...)` in a module that the router imports (the
     `focus/testing.py` pattern).
5. Tasks:
   - Register review kind `connection_auth` (owner integrations, actions accept and snooze,
     impact_scope workspace).
   - Subscriber `tasks.connection_auth_review` on `connection.auth_required`: dedupe_key
     `conn_auth:<id>`, target type `connection`.
6. Add `AuditCases` for `connector.disconnected` and `connector.oauth_state_mismatch` in
   `backend/tests/audit_cases.py`.
7. Frontend (can go to a subagent): `components/settings/connections/*`. It needs:
   - status chips: Connected, Retrying, Sign in needed;
   - "Last synced" / "Never synced";
   - Reconnect only on auth_required;
   - Sync now, Disconnect;
   - wizard and detail views.

   Add `connections` to `sections.ts` and to SCREENS in `routes/settings.$section.tsx`, and add MSW
   handlers. Test at 375 px with DS-01 primitives. T-14 should then pass.
8. Docs:
   - `docs/plan/p3-02-provider-assumptions.md`;
   - the AGENTS.md "new connector" checklist;
   - a Part A A12 row for P3-02.

   Optionally add a Prometheus alert on sync age; otherwise it is a Scott item.
9. Push. CI runs on PR #156. Remove markers one at a time, citing the CI run id. Finish the PR body
   (deviations with sources, Context7 citations, shared-file edits, ingest_items pending), mark it
   ready, comment `@coderabbitai review` once, and run the review loop.
10. Send "#156 MERGE-READY at <sha>" to main. Delete HANDOFF.md in a chore commit.

## Design facts (verified in c0/c1; keep them)

- DBOS 3.1.0 refuses `queue_partition_key` together with `deduplication_id` (`dbos/_queue.py:625`).
  So there is no partition key: the dedup id is `sync:<id>` with return-existing, and per-provider
  limits come from `core.ratelimit.SlidingWindows`. Deviation; cite the source.
- MCP SDK pinned at 1.30.0 (httpx, `streamable_http_client(url, http_client=)`, `ClientSession`,
  `TokenStorage` with 4 methods, helpers in `mcp.client.auth.utils`, `PKCEParameters.generate()`).
  Context7 shows the v2 docs (httpx2). Follow 1.30.0 and cite both.
- `tasks.api` imports `integrations.api`, so integrations must never import tasks.
- One step per page that fetches and persists, so a raw page is never a DBOS step output. On
  resume the page is refetched, which T-05 asserts.
- The framework owns only providers registered through `register_provider`. Calendar's "google",
  "github" and the seed connections are excluded. Calendar's legacy statuses `needs_reauth`/`error`
  are still accepted on write and read back as `auth_required`/`degraded`.
- Status `syncing` is set in `begin_sync`. The tick only syncs status ok or degraded.
  `next_sync_at` NULL means the connection is due now.
- Audit only `connector.connected` (the worker, under the starting user's actor),
  `connector.disconnected` and `connector.oauth_state_mismatch` (its own transaction).
- `FakeSource`:
  - stateless by `cursor["page"]`;
  - reads `DEFAULT_PAGES` at call time;
  - `demo_pages(n, per)` gives external ids `msg-p<page>-<n>`;
  - appends `clock.now()` to `started` before any await (T-07).

  Token strings must not look like credentials, so use names like `fake-access-1`.

## Deviations (accepted by the coordinator; list each with its source in the PR body)

- Dedup ID instead of a partition key.
- Sliding-window limiter.
- One fetch-and-persist step per page.
- Token refresh under a row lock.
- MCP SDK 1.30.0 (httpx) instead of the v2 docs (httpx2).

## Scott items

- `ingest_items` / `POST /ingest` stays PENDING (decision 80 defers P3-01 and the provider
  connectors).
- The `account` placeholder is the connection id until a provider reports the account identity.
- A Prometheus alert on sync age, if it is not added.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/integrations
make check            # semgrep meta-test fails only in the sandbox (read-only ~/.semgrep)
make test-int         # bare, at most once per continuation; prefer CI on PR #156
```
