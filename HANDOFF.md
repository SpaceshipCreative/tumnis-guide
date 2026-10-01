# HANDOFF: P1-18 (Close the day and local metrics)

I stopped here because the context watcher sent HANDOFF NOW. This is a clean point: the branch builds, and every test that passes has had its marker removed.

- Branch: `wp/P1-18`. Pushed up to 3456ed7. Push the commits after it with `/usr/bin/git push origin HEAD:wp/P1-18`.
- PR: not opened yet. Title: `[P1-18] impl: close the day and local metrics`.
- CodeRabbit: not requested yet. CI: none (CI only runs on PRs).
- Migrations: none. P1-18 adds no table.

## Coordinator decisions (binding)

The coordinator approved option A on 2026-10-01. The locked import contract wins over the plan's file placement, so:

- **Routes.** All three P1-18 routes are in **planning**, on a new unprefixed `root_router` in `planning/router.py`:
  - `GET /v1/day/{day}/summary`
  - `POST /v1/metrics/open` (204, idempotent)
  - `GET /v1/metrics/summary?from=&to=`

  `app.py` gets one added line, `routers += module_routers("root_router")`. The metrics routes also depend on usage's module flag.
- **Why not usage.** The plan puts these routes in usage, but `search-usage-subscribe-only` and AGENTS.md boundary rules 2 and 4 forbid usage from reading tasks, plans or auth.
- **What usage keeps.** The pure metric functions in `usage/rules.py`, re-exported through `usage.api`, plus `record_open(s, workspace_id, day)` and `open_days(s, from, to)` on its own `usage_counters` table. `app_open` is counted on the workspace's LOCAL day; the event counters stay on UTC days.
- **Exit gate.** It reads `plan_items.accepted_at`, `removed_at` and `swapped_from_task_id` (planning's own tables), not `decision_log`.
- **A1.6** (`frontend/e2e/journeys/J7.spec.ts`) keeps its `test.fail()`. It is a Scott item: it needs the phase 1 seed (Acme site, tumnis-master), and the seed's Today-status tasks change the exact "Rolls over" count. Do not edit the locked test.
- **PR body.** List the deviations above with their reasons, and say there is no migration.

## Commits

| SHA | What |
| --- | --- |
| 87a05be | `test(planning): P1-18 spec tests (red)`: T-01 to T-10 red, stubs, `make gen`, live-map entries |
| 3456ed7 | Merge of origin/main (#124). The generated files were regenerated and showed no diff. |
| ffadee1 | `day_summary` in planning/rules.py. T-01 and T-02 green. |
| 6984d3e | The metric functions in usage/rules.py. T-03 to T-05 green. |
| 775ed6e | The routes and api: `planning.api.day_summary`, `record_app_open` and `metrics_summary`, `tasks.api.day_task_facts`, `agents.api.finished_runs`, `usage.api.record_open` and `open_days`. T-06 and T-07 are unmarked but NOT YET VERIFIED, because I stopped `make test-int` before it finished. The commit also adds route tests in `planning/tests/integration/test_day_summary_api.py` (not spec tests). |
| (handoff commit) | Uncommitted frontend work-in-progress (the CloseDayPanel and its markers, listed under "Spec test status") plus this file |

## Spec test status

| Test | Status |
| --- | --- |
| T-P1-18-01 to 05 (unit) | Green, markers removed |
| T-P1-18-06 and 07 (usage integration) | Markers removed, not yet run. Verify with CI or one `make test-int`. |
| T-P1-18-08 (CloseDayPanel "sections and empty states") | Still `test.fails`. The component is written, but the test calls the synchronous `within(panel).getAllByRole("region")` before the summary query resolves. Change the first region lookup to `await within(panel).findAllByRole("region")`. This is my own unmerged test, so editing it is allowed. Then remove `.fails`. |
| T-P1-18-09 (dismissing sends no write) | Green, marker removed |
| T-P1-18-10 (MetricsSection) | Still `test.fails`. MetricsSection.tsx is still a stub. |

## Remaining steps (TDD order)

1. **T-08.** Fix the region lookup described above, check that the test passes, and remove `.fails`.
2. **Dashboard wiring** in `frontend/src/routes/index.tsx`:
   - Add `panel: z.enum(["close"]).optional().catch(undefined)` to `dashboardSearch`. Keep `focus`; P2-15 also edits this file.
   - In `DashboardPage`, add a `Close the day` button visible from 16:00 local (the workspace tz). It navigates with `search: prev => ({...prev, panel: "close"})`.
   - Render `<CloseDayPanel day={planDay} onClose=...>` when `panel === "close"`. Closing drops `panel` from the search.
   - Add a `Close the day` item to the account menu in `AppHeader.tsx`, navigating to `/?panel=close`. First check `AppShell.test.tsx` for a list of menu items.
3. **App-open ping.**
   - Add a hook in `AppShell` that calls `POST /v1/metrics/open` through `apiWrite` with an `idempotencyKey`, once per app start and again after 30 minutes hidden (`visibilitychange`), and only when not `bare`.
   - Add the `appOpen` handler from `src/test/msw/closeDay.ts` to the default `handlers` in `src/test/msw/handlers.ts`. Without it, every `renderRoute` test fails on an unhandled request.
4. **T-10, MetricsSection.**
   - Read `planningGetMetricsSummaryOptions` with from = today − 13 days and to = today (workspace tz from `workspaceQuery`).
   - Show the region "Planned days in a row" with "<n>" and "of 10 working days".
   - Each metric goes in a `[data-metric]` row with a `dt` label: "Daily open rate", "Tasks completed per working day", "Rollover rate", "Estimate error", "Share done by agents", "Agent result acceptance", "Unattended overnight runs".
   - Values: percentages rounded (0.857 shows as "86%"); tasks per day as "2.5"; null shows "No data yet". When `available_after` is set, show "Available after phase N" instead.
   - Show the target text next to each value.
   - Wire it in: add "metrics" to `sections.ts` (label "Metrics"), to `SCREENS` and to the loader switch in `routes/settings.$section.tsx`. Check `routes.test.tsx` and the SettingsNav tests for a list of sections.
5. **Checks.**
   - Run `bash /tmp/claude-1002/P1-18-c0/check.sh` (make check with semgrep's files under $TMPDIR). It must pass. Also run `npm --prefix frontend run typecheck`.
   - Check that A1.6 still compiles. It stays `test.fail()`.
6. Push, open the PR with a body file in `$TMPDIR/P1-18-c1/` (summary, layers, shared-file edits, deviations, Context7 citations, Scott items, then the 🤖 line). Comment `@coderabbitai review` once. Run the review loop from `~/tumnis-coordinator/pr-review-loop.md`. When CI is green and no threads are open, send "#<PR> MERGE-READY at <sha>" to main.

## Shared-file edits so far

- **`backend/tumnis/app.py`:** one line, `module_routers("root_router")`, plus a docstring note.
- **`tasks/api.py` and `agents/api.py`:** functions appended at the end of each file only.
- **`frontend/src/lib/live-map.ts`:**
  - `planningGetDaySummary` added to the task lists.
  - `planningGetMetricsSummary` added to NOT_LIVE.
- **Generated files:** the client and `schemas/openapi.json`, via `make gen`.

## Docs consulted (cite in the PR)

- **Context7, TanStack Router** (`/tanstack/router`): `validateSearch` with zod, and the `useNavigate` search updater.
- **Context7, MSW** (`/mswjs/mswjs.io`): the life-cycle events `request:start` and `removeListener`. I also checked the MSW 3.0.0 typings in node_modules.
- **Still to look up in Context7:** TanStack Query (`queryOptions` with `staleTime`), and SQLAlchemy 2.1 (`func.bool_or`, `outerjoin`).

## Gotchas

- **Git and shell commands.** Use `/usr/bin/git`. Heredocs combined with `cd` are refused in this worktree; edit with the Write/Edit tools or with Python scripts.
- **pytest output.** `rtk` condenses pytest output. Use `rtk proxy uv run pytest ...` for the full output.
- **`make test-int`.** It was stopped mid-run. I could not check for leftover containers (`docker ps` was denied from the sandbox).
- **DashboardPage tests.** They use `renderRoute("/")`, which renders AppShell, so the app-open handler must be a default MSW handler.
