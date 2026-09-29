# P1-10 handoff (Working hours and free blocks)

Branch `wp/P1-10` (pushed to origin). **No PR opened yet.** CI: not run on this branch yet
(Actions works again since the repo went public; use CI and the normal review loop).

## Commits

| SHA | What | State |
| --- | --- | --- |
| 236623f | `test(planning): P1-10 spec tests (red)`: T-P1-10-01..13 strict xfail / `test.fails`, interface stubs, generated client | make check green (bar a load-flaky Hypothesis deadline in `core/tests/unit/test_audit_redaction.py`) |
| 8bab1a0 | `feat(calendar): free_blocks ... (T-P1-10-01 to 05)` + `clip`/`subtract` helpers with their own tests (`test_interval_helpers.py`) | green, markers removed |
| 4fedc15 | `feat(planning): working_window ... (T-P1-10-06 to 09)`; 100% line coverage on `planning/rules.py` | green, markers removed |
| (WIP commit after 4fedc15) | backend table/endpoint/cache (T-10, T-11 markers removed) + frontend strip and settings section (T-12, T-13 flipped to `test`) | **NOT verified**: see below |

## What the WIP commit contains

Backend:
- `planning/migrations/0001_working_hours.py`: revision `planning_0001` (branch label `planning`,
  depends_on `auth_0001`); `working_hours(weekday smallint, start_local time, end_local time)`,
  checks `weekday BETWEEN 0 AND 6` and `end_local > start_local`, unique `(workspace_id, weekday)`.
- `planning/models.py`: `WorkingHours`.
- `planning/api.py`: `get_working_hours`, `put_working_hours` (week version = sum of row versions,
  per-workspace advisory lock, StaleVersion 409, `WorkingHoursInvalid` 422, audits
  `settings.changed` {section: "working-hours", fields: ["weekday_N"]}, invalidates tag),
  `day_calendar` (cached in `free_blocks` namespace, TTL 300 s, tags `free_blocks_tag(ws)` and
  `auth.workspace_settings_tag(ws)`), `day_calendar_key`, `free_blocks_tag`.
- `planning/router.py`: `GET /v1/plan/{day}/calendar`; `settings_router` `GET/PUT /v1/settings/working-hours`.
- `planning/events.py`: `@subscribe("calendar.synced", name="planning.drop_day_calendars")`.
- `auth/api.py`: `workspace_settings_tag()`; `_put_workspace_settings` also invalidates that tag.
- `calendar/api.py`: `free_blocks = _free_blocks` re-export (planning may only import calendar.api).
- `integrations/api.py`: `connection_accounts(session, ids)` (one query).
- `core/tests/integration/row_factory.py`: `COLUMN_VALUES` for `working_hours.start_local/end_local`
  (09:00/18:00) — the A0.3 sweep's `minimal_row` hit `ck_working_hours_order` with 00:00/00:00.
- New tests: `planning/tests/integration/test_working_hours.py` (defaults, save moves window +
  drops cache + audit, stale/invalid refusals, calendar.synced drops cache).

Frontend:
- `CalendarStrip.tsx` (placeholder while pending, then region "Today's calendar"; free-block and
  event lists with aria-labels; now marker), `dayCalendarQuery`.
- `WorkingHoursSection.tsx` (Mon–Fri time inputs, zod refine "End must be after start", one PUT).
- Wiring: `sections.ts` (`working-hours`), `settings.$section.tsx` (screen + loader),
  `settings/queries.ts` (`workingHoursQuery`), `DashboardPage.tsx` (strip in left column once the
  workspace timezone is loaded, `localDay` in `format.ts`), `test/msw/dashboard.ts` default
  `dayCalendar(NO_WINDOW)`, `e2e/fixtures.ts` SETTINGS_SECTIONS row "Working hours" -> Save.
- `app.py`: `module_routers("settings_router")` mounted before the generic `/v1/settings/{section}`
  (dynamic, so import-linter does not see app -> planning).

## Exact next steps

1. `make gen` (router docstrings changed after the last gen) and commit any diff.
2. Vitest: `src/lib/live-map.test.ts` T-P0-22-11 fails: the new generated query ops
   (`planningGetDayCalendar`, `settingsGetWorkingHours`) must be added to `LIVE_MAP` or declared
   not live in `frontend/src/lib/live-map.ts`. Then `npx vitest run` all green.
3. Add non-spec Vitest cases for CalendarStrip (no-window day text, error state) if wanted.
4. `make check` (set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` to
   files under `$TMPDIR`).
5. `make test-int` (bare). Last run (before the row_factory fix) failed: 269 A0.3 isolation
   ERRORs, all from `working_hours` in `minimal_row` (now fixed, unverified); plus
   `test_migrations.py::test_stairway_on_seeded_database` (check whether the new
   `planning_0001` revision needs anything; may relate to seeding), `test_relay.py` two tests
   and `test_rclone...` (known local-only timeout) — check if load-related. T-P1-10-10/11 and
   `test_working_hours.py` results not yet seen.
6. `make test` (unit + contract), `make e2e` for `e2e/layout/settings.spec.ts` (new Working hours
   row) and the dashboard specs (strip added to the left column; check no page scroll at
   1280x800).
7. Push, open the PR: title `[P1-10] impl: Working hours and free blocks`, body file in $TMPDIR,
   then `gh pr comment <url> --body "@coderabbitai review"`; run the review loop.

## Deviations from the plan (put in the PR body)

- `Interval` lives in `tumnis.core.types` (re-exported as `calendar.rules.Interval`): the planning
  rules must use it and the rules allow-list (T-P0-01-09) admits only `tumnis.core.types`.
- `planning/rules.py` has its own `local_to_utc` copy (same as P0-19's `rules_recurrence`),
  because `core.clock` is not on the rules allow-list; T-P1-10-08 pins it equals core's.
- Working hours are not seeded at workspace creation: a weekday without a row works
  DEFAULT_HOURS (no workspace-creation hook exists; fixtures insert workspaces directly).
  Same observable behavior.
- `working_window`: a weekend day on Re-plan uses its own row when present (T-P1-10-07 needs
  custom hours on Sundays), else DEFAULT_HOURS; hours a DST gap folds to nothing give None.
- API times are "HH:MM" strings (pattern), not `time`, so the generated zod and the time inputs
  agree.
- Endpoint has no `replan` parameter (Re-plan is P1-11's).
- Cache: TTL 300 s (not in the plan) bounds staleness from event writes that emit no
  `calendar.synced` (deselecting a calendar); timezone change invalidates through a new auth
  cache tag.
- Extra `integrations.api.connection_accounts` for the `account` field.

## Scott items

- None blocking. Done-checklist items needing Scott: strip showing real free blocks from his
  accounts; phone-width check.

## Verify commands

```
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/calendar tumnis/modules/planning
npx --prefix frontend vitest run   # from frontend/
make check
make test-int
```
