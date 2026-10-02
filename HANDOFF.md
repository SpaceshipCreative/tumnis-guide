# HANDOFF: JOURNEYS-B (J2 A1.1, J3 A2.1, J8 A2.6), continuation c2 next

Instructions: ~/tumnis-coordinator/prompts/wave1/JOURNEYS-B.txt (binding). Branch `fix/journeys-run-focus`.
Push with `/usr/bin/git push origin HEAD:fix/journeys-run-focus`. Draft PR **#161**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/161). origin/main merged up to 3f678e7 (#152, #160).
CodeRabbit: not requested yet (draft). No review threads.

## Commits on the branch (newest first)
- `6215d241` merge origin/main (#152 agents_0011, #160 hosted keys); `make gen` clean after it
- `35a56cc0` feat(tasks): Just added list and task history routes (A1.1)
- `b9bc199a` test(tasks): Just added list and task history for A1.1 (2 integration tests, green locally)
- `f00366e3` fix(focus): the focus-wake tick settles the event pipeline first
- `140282c8` test(focus): a focus-wake tick right after Start reaches the new session (red before f00366e3, green after)
- `7e070980` test(e2e): A2.1 passes; marker off (CI run 36962009623) -- J3 marker REMOVED
- `acb7b85a` (c0) fix(frontend): A2.1 review badge and a decision that met only a re-rank
- (this commit) chore: JOURNEYS-B handoff

## State per journey
| Journey | State |
| --- | --- |
| J3 / A2.1 | **DONE, unmarked.** CI run 36966476881 (head f00366e3): J3 passes unmarked on phone and laptop. |
| J8 / A2.6 | Two causes. (1) FIXED in f00366e3: the focus-wake tick ran before the new session workflow existed. (2) STILL FAILING: a race between the Start click's POST and the test's clock move (details below). Fix designed, test drafted, NOT committed. |
| J2 / A1.1 | Backend done (routes, generated client, live map). Frontend NOT started. |

## J8 cause 2 (next step 1)
Evidence: e2e-logs artifact of failed run 36962009623 (download needs `allowed_domains: *.blob.core.windows.net`;
a copy is in /tmp/claude-1002/JOURNEYS-B-c1/ci-36962009623/). error-context.md shows the page at 10:25 with the focus
bar reading "Write proposal 0 min": the session started at 10:25, not 10:00. api.log: `POST /v1/test/clock` (to +25)
completed at 03:55:24.671, the Start `POST /v1/tasks/{id}/status` at 03:55:24.692. The Start request most likely
arrives first, but TumnisRoute reads the clock only after rate/auth/_authorize/idempotency/session
(`now=_clock(request).now()` in tasks/router.py), so the clock POST (no work) overtakes it.
Note: with fakes the server clock is FIXED (OverridableClock) and the Playwright page fixture posts the page's clock to
/v1/test/clock once a second.

Planned fix (fakes only, test harness semantics, core): a pure-ASGI `WritesInFlight` registry + middleware, added in
`create_app` only when `tumnis_adapters == "fake"` (outermost, after `metrics.RequestMetricsMiddleware`), registering
every http write (POST/PUT/PATCH/DELETE) whose path does not start with `/v1/test/` for its duration; `set_clock` in
`core/testing_routes.py` first waits (poll ~5 ms, bounded ~2 s) until the writes open at its arrival have finished,
then sets the clock. Store the registry on `app.state`.
Draft test (NOT in the repo): /tmp/claude-1002/JOURNEYS-B-c1/test_test_clock_waits_for_writes.py.draft, meant for
`backend/tumnis/core/tests/integration/`. Its first run failed for a harness reason, not the bug: the inner route's
`request: Request` annotation became a string under `from __future__ import annotations` and FastAPI read it as a
query param (422 `query.request: Field required`). Fix the draft (drop the future import in that file, or import
Request at module level), confirm it is red for the right reason (write reads 10:25), then implement.
After that, also consider whether the planner tick is fast enough in CI: in CI run 36962009623 J8 got past both
DAY_ONE and DAY_TWO plans, so it is.

## A1.1 (coordinator decision 83; Scott may override) -- next step 2
Done (backend): `GET /v1/just-added` (session; `api.just_added`: caller's own tasks, created today in the workspace
tz by the DB clock that stamps created_at, status backlog, not trashed, newest first, max 3; reading of "not yet
planned or done" = still in Backlog -- name it in the PR body) and `GET /v1/tasks/{id}/history` (`api.task_history`:
Page[TaskChangeOut] newest first, `fields` = changed UNDO_FIELDS or created/trashed/restored, `actor`, `by_you`,
`undone`). Generated ops `tasksListJustAdded`, `tasksListTaskHistory` are in LIVE_MAP (task lists / task details),
TASK_VIEWS (lib/task-cache.ts) and TASK_READS (dashboard/queries.ts).
To do (frontend), per J2.spec.ts lines 216-313:
- Dashboard "Just added" section, FIRST in DOM order on the left column (before CalendarStrip) so
  `taskCards(page, title).first()` hits it, hidden when empty; rows merge the capture queue's pending rows
  (`usePendingTasks` + `pendingRow`, deduped) with the server list. Add it to the `/` route loader too.
- Row: `<li>` with the title as a button (opens the drawer), `LabelChip` (data-testid label-chip, aria-describedby a
  VISIBLE one-line reason, menu option "Human"; reuse `useLabelOverride`), a first-action element with
  `data-testid="first-action"` + `data-state` whose ONLY text is the value (toHaveText is a full match; do NOT change
  `FirstActionLine`, its tests assert on it), and `data-testid="estimate-chip"` reading e.g. "20 min".
- Dashboard route `validateSearch` gains `task` and `run` (copy routes/projects.$projectId.tsx); mount `TaskDrawer`
  on the dashboard (`?task=`), Escape closes it.
- TaskDrawer: add `TaskLabelChip` (labelChip(drawer) must read "Human" after reload) and a `<section aria-label="History">`
  list from `tasksListTaskHistoryOptions`, newest first; the Label entry shows "you" when `by_you`. It already has the
  "Acceptance criteria" list.
- Keep A0.1 (J2.spec.ts 40-208) green: no scroll at 1280x800. In A0.1 the captured task is moved to Today, so Just
  added is empty there; seed tasks are created by the system actor, so they never show.
- Vitest for the new components; then diag-run J2 on the private stack.

## Local diagnosis (decision 76, throwaway, NEVER commit)
- Scripts in /tmp/claude-1002/JOURNEYS-B-c1/: `stack.sh up|down` (private stack, project tumnis-jb, port 18983,
  rebuilds image tumnis:jb from the worktree; currently UP with the f00366e3+A1.1-backend code), `diag.sh J8|J2
  [laptop|phone]` (copies the spec minus its `test.fail();` line into frontend/e2e/zz-diag-jb/, runs it with
  E2E_BASE_URL, deletes the folder). Both need `dangerouslyDisableSandbox` (permission prompt).
  `run-int.sh <pytest paths>` runs integration tests (Docker, same). `check.sh` runs make check with semgrep paths.
- Host load is 20-35 on 32 cores: locally build_plan's gather_step takes 5-8 s and the maintenance queue waits up to
  5 s, so local J8 runs fail at the DAY_ONE plan (404). Environment, not a product bug; CI gets past it.
- e2e junit.xml does not record failures of expected-to-fail tests; error-context.md in the e2e-logs artifact (only
  uploaded when the e2e job fails) does show the page at the failure.

## CI
- Run 36966476881 (head f00366e3): all jobs pass (e2e: J3 green unmarked; J8, A1.1 still expected-fail). preview pending as always.
- Run 36967858269 (head 6215d241) was in progress at handoff.

## PR body items (accumulate; draft at /tmp/claude-1002/JB/pr-body.md from c0)
- J3 fixes (c0) and marker removal (CI run 36962009623).
- J8: focus-wake settle (f00366e3); the clock/write race fix once done.
- A1.1 per decision 83 + the "still in Backlog" reading.
- Docs cited: Context7 `/dbos-inc/dbos-docs` python/reference/client.md (`list_workflows_async` name/status filters),
  pinned dbos 3.1.0 (signature checked in .venv `dbos/_client.py`).
- Follow-up, not fixed: `/v1/test/reset` doesn't cancel pending focus_plan/focus_session workflows, so they hear later tests' ticks.

## Denials and Scott items
- c0: one auto-mode classifier denial on a read-only grep (not retried).
- c1: one classifier denial ([Security Test Removal]) on a harmless `ls/grep` right after the J3 marker edit. Per decision
  70/78 I read CI run 36962009623 first-hand ("Expected to fail, but passed" on both projects) and retried the removal
  ONCE as a commit citing the run; it went through (7e070980).
- No Scott items. Decision 83 is the coordinator's default; Scott may override.
