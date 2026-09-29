# HANDOFF: P1-09 Google Calendar connector

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-09`, branch `wp/P1-09`. Main (bcbae03, P0-18) is already merged in. Read `AGENTS.md`, the prompt footer rules, and the plan section (lines about 9756 to 9873 of `docs/IMPLEMENTATION-PLAN-DETAILED.md`) first.

## Done

| Commit | What |
| --- | --- |
| bcbae03 (merge) | main merged: P0-18 added `calendar.api.seed_event` (seed writer "event", provider `seed`), `integrations.api.seed_connection`, `EventSeed.fetched_at`. Build on them; don't duplicate them. |
| 90eee5f | `test(calendar): P1-09 spec tests (red)`. All 12 spec tests are strict xfail (`spec:P1-09`), plus interface stubs. `make check` was green at this commit. |
| 3718493 | `feat(calendar): map_event, sync_window and needs_refresh rules`. Implemented in `rules.py`, but no marker is removed yet because the recordings don't exist. |

## Spec test status (all still red)

- T-01 `calendar/tests/contract/test_google_connector.py::TestGoogleCalendarOnRecordings`, plus `TestGoogleCalendarFake` (an extra fake class the P0-09 meta-test needs).
- T-02 `tests/unit/test_map_event.py`: parametrized over `tests/recordings/google_calendar/*.json`. It falls back to a `no-recordings` id when the folder is empty.
- T-03, 04, 05, 09, 10, 11: `tests/integration/test_sync.py`.
- T-06, 08: `tests/integration/test_oauth.py`.
- T-07: `tests/unit/test_scopes.py`.
- T-12: `frontend/src/components/settings/CalendarSection.test.tsx` (`test.fails`).

Test helpers hold no assertions and may be edited:
- `tests/integration/_calendar.py`
- `tests/integration/conftest.py` (fixtures `app_db`, `google`, `oauth_client`)
- `tests/integration/_page_log.py` (subprocess probe for the kill test)
- `tests/replay.py` (stub: `recorded_connector()`)
- `frontend/src/test/msw/calendar.ts`

Never change assertions in the test files.

## Remaining TDD steps, in order

### 1. T-07 and T-02

- Implement `api.consent_url` with `urllib.parse.urlencode`:
  - base `https://accounts.google.com/o/oauth2/v2/auth`
  - `client_id`, `redirect_uri`, `response_type=code`, `scope` = the sorted `READONLY_SCOPES` joined by spaces
  - `access_type=offline`, `prompt=consent`, `state`, `code_challenge`, `code_challenge_method=S256`
  - Remove the T-07 marker.
- Add `calendar_id: str | None = None` to `EventRecord`. Then run `make gen` (the schema `entities/event` changes), update `_columns` to write `calendar_id`, and add `calendar_id` to `models.Event`.
- Write the recordings under `backend/tumnis/modules/calendar/tests/recordings/google_calendar/`. Use one file per event case, in ConnectorContract format: `{"raw": RawItem, "expected": [EventRecord JSON], "notes": "Scrubbed: ..."}`.
  - The cancelled case has `"expected": []` and `"tombstone": "<calendar_id>:<id>"`.
  - `raw.record_type` is `"event"`, and `raw.external_id` is `"<calendar_id>:<event id>"`.
  - `raw.payload` is `{"calendar_id", "self_email", "time_zone": "America/New_York", "event": <Google event JSON>}`.
  - `fetched_at` is `2026-03-09T12:00:00Z`.
- Account A is `avery@example.com` (self, primary calendar id). Account B is `blake@example.org`.
- Planned events (local times are America/New_York; EDT from 2026-03-08):

| Case | Page | When | Details | Busy |
| --- | --- | --- | --- | --- |
| `a-kickoff` | A page 1 | Mar 9, 09:00 to 10:00 | title "Acme kickoff", iCalUID `kickoff-shared@example.com`, attendees avery (self, accepted) and blake | yes |
| `a-weekly_20260310T150000Z` | A page 1 | Mar 10, 11:00 to 11:30 | recurring instance, `recurringEventId` `a-weekly`, "Weekly sync" | yes |
| `a-holiday` | A page 1 | all day Mar 11 (end date Mar 12) | transparency transparent | no |
| `a-1on1` | A page 2 | Mar 12, 10:00 to 10:30 | | yes |
| `a-declined` | A page 2 | Mar 10, 13:00 to 14:00 | self `responseStatus` declined, "Vendor demo" | no |
| `a-focus` | A page 2 | Mar 10, 14:00 to 16:00 | transparent, "Focus time" | no |
| `a-ooo` | A page 3 | all day Mar 13 | transparency opaque, "Out of office" | yes |
| `a-weekly_20260317T150000Z` | A page 3 | | status cancelled; Google sends only id, status, `recurringEventId`, `originalStartTime` | tombstone |
| `a-review` | A page 3 | Mar 9, 15:00 to 15:30 | "Design review" | yes |
| `b-kickoff` | B page 1 | same as `a-kickoff` | same iCalUID, blake self accepted, title "Acme kickoff" | yes |
| `b-lunch` | B page 1 | Mar 9, 12:00 to 13:00 | "Client lunch" | yes |

- Page files go in the `pages/` subfolder, so the contract's glob doesn't see them:
  - `pages/account_a_page1.json`, `account_a_page2.json`, `account_a_page3.json`, `account_b_page1.json`, `account_a_cancelled.json`
  - Shape: `{"calendar_id", "self_email", "request": {"pageToken", "timeMin", "timeMax"}, "response": <full events.list JSON: kind, summary, timeZone, items, nextPageToken>, "notes"}`
  - Page tokens are `a-p2` and `a-p3`. The kill test expects the log lines `first`, `a-p2`, `a-p3`.
  - `account_a_cancelled` holds `a-kickoff` with status cancelled.
- `oauth/token_invalid_grant.json`: `{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}`, plus recorded token and calendarList responses if the replay needs them.
- Remove the T-02 marker.

### 2. T-01: the connector over the recordings

The `GoogleCalendar` connector lives in `api.py`, per the plan:
- `sync(cursor)` parses a `CalendarCursor`. With no cursor, it starts at the constructor window.
- It calls `api.list_events`. Items become one `RawItem` per event, with the wrapper payload above; the `time_zone` comes from `response.timeZone`.
- Cancelled items (`rules.map_event` gives a `Tombstone`) go to `SyncPage.deleted`.
- `next_cursor`: the same index with `nextPageToken`, or the next calendar index with no token, or `None` with `has_more=False`.
- `map(raw)` returns `[EventRecord(**mapped.model_dump(), fetched_at=raw.fetched_at)]`, or `[]` for a tombstone.
- `health()` returns "degraded" when `status == "needs_reauth"`.
- The fake factory's defaults must replay account A. `fakes["integrations.connector.google_calendar"]` is built with no deps, so default `api=FakeGoogleCalendar()`, `calendar_ids=["avery@example.com"]`, `self_email=...` and a window.

Implement the adapters:
- `adapters/google.py`: `GoogleCalendarApi(Adapter)`, name `"calendar.google"`, `guarded_client(Settings().net_policy() or an injected policy, resolver=, inner=transport)`.
  - Token endpoint `https://oauth2.googleapis.com/token`. A 400 with `invalid_grant` raises `GrantRevoked`.
  - Map 5xx and 429 to `AdapterUnavailable` and other 4xx to `AdapterRejected`.
  - `calendarList`: `https://www.googleapis.com/calendar/v3/users/me/calendarList` (loop over pages).
  - `events.list`: `singleEvents=true`, `showDeleted=true`, `maxResults=250`, `timeMin`, `timeMax`, `pageToken`.
  - Use `RetryPolicy(max_attempts=1)`, since it runs inside DBOS steps.
- `adapters/fake.py` (`FakeGoogleCalendar`) replays `pages/*.json` by `(calendar_id, page_token)`. Methods:
  - `script_pages(calendar_id, [responses])` overrides the next sync's pages.
  - `revoke(refresh_token)` makes `refresh` raise `GrantRevoked`, using the recorded body.
  - `fail_events(calendar_id, error)`.
  - `refreshed` is a list of refresh tokens used.
  - `calls` is a list of `(op, kwargs)`.
  - `exchange_code`: `code-a` or `code-b` maps to account a or b, with tokens `fake-access-<x>` and `fake-refresh-<x>`.
  - `list_calendars` by access token.
  - Keep a `health_state()`.
- Register in `adapters/__init__.py`: `register_adapter("calendar.google", port=GoogleCalendarPort, real=..., fake=FakeGoogleCalendar)`.
  - The P0-09 meta-test then needs a contract suite `tests/contract/test_google_contract.py`: a base `GoogleContract(AdapterContract[GoogleCalendarPort])`, then `TestGoogleFake` (impl "fake") and `TestGoogleRecorded` (impl "recorded", the real API over `tests/replay.py`'s `httpx.MockTransport` with `ScriptedResolver({"www.googleapis.com": ["142.250.0.10"], "oauth2.googleapis.com": [...]})`).
- **Import-cycle rule:** `adapters/*` must NOT import `calendar.api`. `acyclic_siblings` forbids calendar `api <-> adapters`, even for `TYPE_CHECKING` imports. That is why `register_connector(...)` sits at the bottom of `api.py` and `api.py` imports `adapters.port`.
- `tests/replay.py::recorded_connector()` returns `resolve("integrations.connector.google_calendar", "real", api=GoogleCalendarApi(<replay transport>), calendar_ids=[avery], self_email=avery, window=..., clock=FixedClock(T0))`.
- Remove the class markers on both contract classes.

### 3. T-03, 04, 05: tables and upserts

- Migration `calendar/migrations/0002_calendar_accounts.py`:
  - `revision = "calendar_0002"`, `down_revision = "calendar_0001"`, `phase = "expand"`.
  - `create_tenant_table("calendar_accounts", connection_id FK connections, google_email text, calendars jsonb default '[]', selected_calendar_ids text[] default '{}', status text default 'connected' CHECK in ('connected','needs_reauth'), last_sync_at timestamptz, unique index (workspace_id, connection_id))`.
  - `op.add_column("events", calendar_id text)`.
  - `all_day` and `provider_url` already exist from P0-12's `calendar_0001` and `canonical_columns`, so this is a deviation from the plan text; note it.
  - Add `("calendar_accounts", "status"): "connected"` to `COLUMN_VALUES` in `backend/tumnis/core/tests/integration/row_factory.py` (issue #31).
  - The `calendars jsonb` column is an addition beyond the plan: the UI needs the calendar names.
- Add `CalendarAccount` to `models.py`.
- `api.ingest_events_page(ctx, connection_id, connector, page, session=)`:
  - `integrations.store_raw_payloads` on the page items, keyed `("event", external_id)`.
  - `connector.map` on each item.
  - `upsert_records(... raw_ids ...)`, then `core.canonical.soft_delete_records(s, _events, connection_id, page.deleted)`.
- `events_between(ctx, start, end)`: live rows where `start_at < end AND end_at > start`, ordered by `start_at`. Include seed-connection events.
- `list_accounts`, `connect_account(ctx, tokens, calendars)`:
  - Upsert the connection (kind "calendar", provider `google_calendar`, account = the primary calendar id / email, status "ok").
  - Put the sealed credentials.
  - Upsert the `calendar_accounts` row (status connected; default selection is the primary calendar).
  - Return `CalendarAccountOut`.
- New integrations api functions (P3-02 will reuse them):
  - `upsert_connection`
  - `put_credentials`: `settings_store.seal_for_workspace`, aad `b"connections:" + id`; sets `credentials_enc` and `key_version`.
  - `get_credentials`: already stubbed.
  - `get_sync_cursor`, `save_sync_cursor`: use the `sync_state` table, one row per connection.
  - `set_connection_status`
- Remove the markers as each test goes green. T-03 only needs `ingest_events_page` and the fake; T-04 and T-05 need step 5's workflow.

### 4. T-06, 08, 10: OAuth

- Settings section: `register_section(SettingSection(OAUTH_SECTION, GoogleOAuthClient, secret_fields={"client_secret"}))` in `api.py`.
- Add an `oauth_pending` table in a new `integrations_0002` revision (A12 lists `oauth_pending` for later; this WP's step 7 shares OAuth in integrations, so put it there and update A12):
  - `provider`, `state_hash bytea` (unique with workspace)
  - `verifier_enc`, `code_enc` (nullable), `key_version`, `redirect_uri`, `expires_at`, `used_at`
  - integrations api `begin_oauth`, `accept_oauth_code` (validate the state hash, 10-minute expiry, one use, seal the code), `read_oauth_grant`, `consume_oauth_grant`
  - PKCE: verifier = `secrets.token_urlsafe(64)`; challenge = base64url(sha256), unpadded.
- Router `calendar/router.py`, `v1_router("calendar", prefixed=True, tags=["calendar"])`:
  - `GET /oauth/start` (`auth="session"`) returns `{url}`. `redirect_uri` = `settings.public_base_url + "/v1/calendar/oauth/callback"`. It needs `request.app.state.settings`; check `public_base_url` in tests.
  - `GET /oauth/callback?code&state` (session): write in its own `tenant_session`, commit, then enqueue through the api's DBOSClient, then answer 302 to `/settings/calendar?connecting=1`. A bad or used state answers 400 `oauth_state_invalid`. Enqueue with `workflow_name="calendar_oauth_exchange"`, `queue_name="sync"`, `workflow_id=f"calendar-oauth:{pending_id}"`, args `(workspace_id, pending_id)`. Expose a public `dbos_client()` in `core/deadletter.py` (currently private `_dbos_client`) and list it as a core edit.
  - `GET /accounts` (`unpaginated_reason`).
  - `PUT /accounts/{id}/calendars {selected_calendar_ids, version}`: idempotent, versioned. Look the row up for a 404 before the body rules (issue #28). Soft-delete events of deselected calendars.
  - `POST /accounts/{id}/sync`: 202, enqueues `calendar_connector_sync`.
- Workflows (`calendar/workflows.py`):
  - `use(api, clock)` swaps module globals and returns the previous `(api, clock)`.
  - `calendar_oauth_exchange` step: read the grant, `exchange_code`, `list_calendars`, then `connect_account` and `consume_oauth_grant` in one transaction.
  - Refresh inside the sync. `GrantRevoked` means: account `needs_reauth`, connection status `needs_reauth`, and the workflow returns `{"status": "needs_reauth"}` with no event.
  - Tests `use()` the fake and the clock.

### 5. T-09, 11: sync workflow

- `@DBOS.workflow(name="calendar_connector_sync") async def connector_sync(workspace_id, connection_id)`:
  - `begin_sync` step: token refresh check. If no cursor is stored, compute the window with `rules.sync_window` (today and tz from `auth.api.get_workspace_settings(ctx).timezone`) and save the initial cursor. Return the window.
  - Loop over the `sync_page(ws, conn, page_no)` step: load the cursor from `sync_state`, build the connector (`build_connector`), `connector.sync(cursor)`, then in ONE transaction `ingest_events_page` and `save_sync_cursor(next)`. After commit, `faults.killpoint(f"calendar.sync.page_{page_no}.committed")`. Return `has_more`.
  - `finish_sync` step: clear the cursor, set `last_sync_at` (clock), and `outbox.emit(CalendarSyncedV1(connection_id, window={start, end}))` in the same transaction.
  - Return `{"status": "synced", "pages": n}`.
  - Steps: `@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=0.1)`, or similar.
- `calendar/payloads.py`: `@event_type("calendar.synced", 1) CalendarSyncedV1`, re-exported by `events.py`. Add the fixture `backend/tests/contract/fixtures/events/calendar.synced/v1.json`, then run `make gen`.
- Worker (`tumnis/worker.py`, shared file):
  - `DBOS.register_queue("sync", ...)` in `register_queues`.
  - Schedule `calendar-sync-tick`, `*/10 * * * *` on the sync queue. It enqueues `connector_sync` per connected account in every workspace (`core.audit.workspace_ids`).
  - `tumnis/wiring.py` needs `load_workflows()` (imports each module's `workflows`) so the worker registers them. The `_page_log` probe also imports calendar workflows.
- Kill test notes:
  - The subprocess inherits `MASTER_KEY_FILE` and `CALENDAR_FAKE_PAGE_LOG` through the environment.
  - It uses the SystemClock, so the test connects with `now=datetime.now(UTC)`.
  - The fake replays account A regardless of the window.
  - Run it 20 times (Done checklist).

### 6. T-12: Settings > Calendar

- Add `"calendar"` to `frontend/src/components/settings/sections.ts`, the `SCREENS`/loader in `routes/settings.$section.tsx`, and a query in `queries.ts`.
- `CalendarSection({openUrl = (u) => window.location.assign(u)})`:
  - Each account is a `role="group"` with `aria-label` = email.
  - Show "Last synced …" or the "Needs reauth" text, and a "Reconnect" button.
  - Calendar checkboxes labeled with the summary. A toggle sends `PUT` with `{selected_calendar_ids, version}` through `apiWrite` (Idempotency-Key).
  - "Sync now" button.
  - "Connect Google account" button: `GET /v1/calendar/oauth/start`, then `openUrl(url)`.
  - An OAuth client form (client ID, plus a write-only secret) via `/v1/settings/calendar.google`.
- Switch the msw helper to the generated types after `make gen`.
- `LIVE_MAP`: add entity `calendar_account` (lists `calendarListAccounts`); put `calendarOauthStart` and `calendarOauthCallback` in `NOT_LIVE`. Every GET op must be listed (T-P0-22-11).
- Check at 375 px.

### 7. Refactor and finish

- Keep the OAuth helpers in integrations.
- Update plan Part A (A12): `calendar_accounts.calendars`, `oauth_pending`, the `sync` queue, `calendar-sync-tick`, the workflow names, and `WorkerKiller.enqueue_until_killed` / `restart_until_done` / `imports`.
- Run `make gen` and commit its output.
- Move `frontend/dist` aside before the backend contract and integration layers.

## Decisions and deviations (report these)

- `map_event` returns `MappedEvent | Tombstone`, not `EventRecord`. `rules.py` may not import `core.schemas` or `canonical`, and `fetched_at` is not an input. `connection_id` is dropped; `calendar_tz` is added for all-day dates.
- API functions take `ctx` (and `session=`), like every other module api. `events_between(ctx, start, end)`.
- `connector_sync(workspace_id, connection_id)` needs the workspace for the tenant context. DBOS names are `calendar_connector_sync` and `calendar_oauth_exchange`, to avoid clashing with P3-02's generic `connector_sync`.
- `register_connector` sits in `api.py`, not `adapters/__init__`, because of the in-module acyclic contract.
- Recordings are one file per event (the ConnectorContract format), with full events.list pages in `pages/`.
- The seed calendar still comes from P0-18's seed writer (provider `seed`), not from the fake connector as the plan says. Not changed, to avoid clashing with P0-18.
- `worker_killer` (in `tests/fixtures/__init__.py`, a shared file) gained `imports=`, `enqueue_until_killed`, `restart_until_done` and `close_client`.

## Gotchas

- The `rtk` hook filters pytest output. Use `rtk proxy uv run pytest ...` to see real results; `-m integration` through rtk showed "No tests collected".
- `test_timeout_fires_inside_budget` can flake under load; rerun it.
- An `AdapterContract` class that names an unregistered adapter fails the P0-09 meta-test. Every registered adapter needs fake and real/recorded contract classes, and `tests/harness` resolves every fake with no deps.

## Verify

```bash
cd /Users/sjordan/Projects/Tumnis-Guide-wt/p1-09 && make check
cd backend && rtk proxy uv run pytest tumnis/modules/calendar -q
rtk proxy uv run pytest -m "not integration and not contract" -n auto -q
rtk proxy uv run pytest -m contract -q
rtk proxy uv run pytest -m integration -n auto -q
cd ../frontend && npx vitest run src/components/settings/CalendarSection.test.tsx
```

## Left for Scott

- A Google Cloud OAuth client (web type) with the redirect URI `https://<private host>/v1/calendar/oauth/callback`, the Calendar API enabled, and the two read-only scopes on the consent screen. Enter the client ID and secret in Settings > Calendar.
- Connecting all business Google accounts (Done checklist).
