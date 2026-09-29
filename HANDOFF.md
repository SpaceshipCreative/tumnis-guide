# HANDOFF 2: P1-09 Google Calendar connector

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-09`, branch `wp/P1-09`. Local `main` was merged at e7865f2 (P0-23 follow-up). The implementation is complete. What's left is verification only (see "Remaining").

## Done (commits on top of the first handoff)

| Commit | What |
| --- | --- |
| 1761acf | T-07: `consent_url` |
| 72b7021 | T-02: recordings (one file per event, plus `pages/` and `oauth/`), `calendar_0002` (`calendar_accounts`, `events.calendar_id`), `EventRecord.calendar_id`, and the `row_factory` status value |
| a75976d | T-01: `GoogleCalendarApi` (`adapters/google.py`), `FakeGoogleCalendar`, the `calendar.google` registration, the connector, `tests/replay.py`, the adapter contract `test_google_contract.py`, and unit `test_google_errors.py` |
| 41b4ab5 | T-03: `ingest_events_page`, `workflows.use` |
| bb939c0 | T-06: integrations `upsert_connection`, `put_credentials`, `get_credentials`, `set_connection_status`, `get_sync_cursor`, `save_sync_cursor`; calendar accounts api; settings section `calendar.google` |
| 3b31537 | T-04, 05, 10, 11: the `calendar_connector_sync` workflow, `payloads.py` (`calendar.synced`), the `sync` queue, `wiring.load_workflows` |
| 491d589 | T-09: kill test green. It passed 20 of 20 in a row, though some of those runs overlapped with later edits. |
| e94319c | T-08: `integrations_0002` (`oauth_pending`), OAuth api, `router.py`, `core.deadletter.dbos_client()`, `LIVE_MAP`/`NOT_LIVE` |
| 69b1699 | T-12: `CalendarSection`, Settings section `calendar`, msw helper on generated types |
| 25c70ec | Plan A12 row for P1-09 |
| b0f528d | The tick enqueues on the `sync` queue, plus `test_tick.py` |
| e7865f2 | Merged main |
| fc602b1 | Dropped the first handoff |
| af0a819 | Fix for the A0.3 tenant sweep: an unknown OAuth state is 404 `oauth_state_unknown` (a used, expired or consumed state stays 400 `oauth_state_invalid`); `/oauth/start` without a client is 404 `calendar_oauth_not_configured`; the path param is now `{calendar_account_id}` so the sweep finds `calendar_accounts` |

All 12 spec tests are green, with every `spec:P1-09` marker removed. The 375 px check passed: no sideways scroll, and buttons and inputs are 44 px tall.

## Remaining (verification only)

1. `make check`. It was green before af0a819; rerun it.
2. Backend layers, with `frontend/dist` absent:
   - unit: 871 passed before af0a819.
   - contract: 147 passed.
   - integration: before af0a819 the only failures were 4 A0.3 calendar cases, now fixed, plus the known relay flake.
3. Rerun `tests/acceptance/test_a0_3_tenant_isolation.py -k calendar`. It has not been rerun since the fix.
4. The last `-n 4` run showed setup ERRORs on several calendar integration tests. They passed serially: 9 of 9 after af0a819. Rerun the full `-m integration -n auto` layer to confirm the errors came from load.
5. `test_notify_wakes_relay_before_poll` is the known load flake. Rerun it alone.
6. Optional: `git merge main` again if main has moved, then `make gen` and commit.

## Decisions and deviations (all of them, for the PR body)

- `map_event` returns `MappedEvent | Tombstone`, not `EventRecord`. `rules.py` may not import `core.schemas`, and `fetched_at` is not an input. It takes no `connection_id` and adds `calendar_tz` for all-day dates.
- API functions take `ctx` (and `session=`): `events_between(ctx, start, end)`, `list_accounts(ctx)`.
- `connector_sync(workspace_id, connection_id)`: the workflow needs the workspace for its tenant context. DBOS names are `calendar_connector_sync`, `calendar_oauth_exchange` and `calendar_sync_tick`, to avoid clashing with P3-02's generic `connector_sync`.
- `register_connector` sits in `api.py`, not `adapters/__init__`, because `acyclic_siblings` forbids calendar `api <-> adapters`.
- Recordings are one file per event (ConnectorContract format). The plan's page files live in `pages/`, and the OAuth answers in `oauth/`, including the recorded token exchange, refresh and calendar list.
- The seed calendar still comes from P0-18's seed writer (provider `seed`), not the fake connector.
- `worker_killer` (shared `tests/fixtures/__init__.py`) gained `imports=`, `enqueue_until_killed`, `restart_until_done` and `close_client`. P1-04 is adding `start`/`stop` there: reuse theirs if P1-04 merges first.
- `calendar_0002` adds only `events.calendar_id`: `all_day` and `provider_url` already existed. `calendar_accounts.calendars jsonb` goes beyond the plan (the UI needs calendar names).
- `oauth_pending` is created here in `integrations_0002`, not by P3-02. Its columns: `provider`, `state_hash`, `verifier_enc`, `code_enc`, `key_version`, `redirect_uri`, `expires_at`, `used_at`. P3-02's planned `connection_id`, `workflow_id` and `iss` must be added by P3-02. A12 is updated.
- The callback enqueues the exchange through the api's DBOSClient; it does not use `DBOS.send`.
- An unknown state answers 404, not 400: the A0.3 sweep requires 200 or 404 for every GET. A used, expired or consumed state answers 400 (T-08). `/oauth/start` without an OAuth client answers 404, also for the sweep. Google's `error=` (no code) redirects to `/settings/calendar?connect_error=1`.
- Cancelled events go only to `SyncPage.deleted`; their raw payloads are not stored.
- The cursor lives in `sync_state`. After the last page it holds a "finished" cursor (index past the last calendar), so a re-run page step fetches nothing. `complete_sync` clears it. `begin_sync` always starts a new window (DBOS records the step, so recovery doesn't rerun it).
- `GoogleCalendarApi` defaults to `NetPolicy(mode="hosted")` (public addresses only; every Google endpoint is public), so module code need not read Settings.
- One Google client per worker process, so one breaker is shared by all accounts. Tokens are passed per call.
- Module schedules come through a generic `workflows.schedules()` hook (`worker.register_module_schedules`, via importlib). A static import of calendar workflows from `worker.py` broke `modules-api-only` through the chain `auth tests -> cli -> worker`.
- "Sync now" is `POST` with `idempotent=False` (with a reason). Its workflow id `calendar-sync:<conn>:<second>` dedupes.
- The fake raises `AdapterRejected` 404 for an unknown calendar, the same as the recorded real side.
- Busy-rule risk: Google omits `transparency` when it is opaque (the default). Under the plan's rule, an all-day event counts as busy only when it is explicitly `opaque`, so real busy all-day events will usually count as free. Scott should confirm.

## Shared-file edits

- `backend/tests/fixtures/__init__.py`: the `WorkerKiller` additions above.
- `backend/tumnis/worker.py`: `SYNC_QUEUE`, `SYNC_WORKER_CONCURRENCY=4`, queue registration, `register_module_schedules()` called in `main`.
- `backend/tumnis/wiring.py`: `load_workflows()`, called at import.
- `backend/tumnis/core/deadletter.py`: public `dbos_client()`.
- `backend/tumnis/core/tests/integration/row_factory.py`: `COLUMN_VALUES[("calendar_accounts","status")]`.
- `frontend/src/lib/live-map.ts`: the `calendar_account` entity; `calendarOauthStart` and `calendarOauthCallback` in `NOT_LIVE`.
- `docs/IMPLEMENTATION-PLAN-DETAILED.md`: the A12 row; `oauth_pending` removed from the P3 row.
- No edits to `pyproject`, `uv.lock`, `Makefile`, `AGENTS.md`, `.importlinter` or `package.json`.

New revisions: `calendar_0002` (down `calendar_0001`) and `integrations_0002` (down `integrations_0001`). Both are module branches; there is no core revision.

## Gotchas

- `rtk` filters pytest output: use `rtk proxy uv run pytest ...`.
- The Fact-Forcing hook blocks `git checkout -- <file>`: recreate files instead.
- Never run `sleep` in the foreground: use `run_in_background` with an until-loop.

## Verify

```bash
cd /Users/sjordan/Projects/Tumnis-Guide-wt/p1-09 && make check
cd backend && rtk proxy uv run pytest -m "not integration and not contract" -n auto -q
rtk proxy uv run pytest -m contract -q
rtk proxy uv run pytest -m integration -n auto -q
rtk proxy uv run pytest tests/acceptance/test_a0_3_tenant_isolation.py -k calendar -q
cd ../frontend && npx vitest run src/components/settings/CalendarSection.test.tsx
```

## Left for Scott

- A Google Cloud OAuth client (Web type) with:
  - the redirect URI `https://<private host>/v1/calendar/oauth/callback`, which must match `PUBLIC_BASE_URL` exactly;
  - the Calendar API enabled;
  - the scopes `calendar.events.readonly` and `calendar.calendarlist.readonly` on the consent screen.
- Enter the client ID and secret in Settings > Calendar.
- Connect all business Google accounts (Done checklist).
- Confirm the all-day busy rule (above).
- If Google refuses the private-host redirect, the plan's fallback (a loopback `tumnis calendar connect` CLI) is not built.
