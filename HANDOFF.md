# P2-15 handoff (Focus events engine and focus bar), continuation c1 -> c2

Two branches:
- `wp/P2-15` = PR **#127** (https://github.com/SpaceshipCreative/tumnis-guide/pull/127),
  "[P2-15] impl: focus events engine and focus bar": the backend. Push with
  `/usr/bin/git push origin HEAD:wp/P2-15`.
- `wp/P2-15-impl-2` (pushed, no PR yet): the frontend, branched from `wp/P2-15` at
  `ecac044`. c1 worked on it in a second git worktree at `/tmp/claude-1002/P2-15-c1/impl2`
  (branch `agent/P2-15-impl-2`, node_modules installed). A fresh agent can instead merge
  `origin/wp/P2-15-impl-2` into a throwaway branch and push `HEAD:wp/P2-15-impl-2`.

Scratch: `/tmp/claude-1002/P2-15-c1/` (PR body `pr-body.md`, drafts below, int1.log).

## Commits on wp/P2-15 (since c0's handoff `f57fd15`)

- `123e8be` feat(focus): workflows, subscribers, routes, focus-wake tick (+ all shared edits)
- `cdbba81` test(focus): unmark T-P2-15-08..15; settle waits for parked focus workflows
- `e9f2efb` merge origin/main (#125)
- `ecac044` chore: remove the handoff note (this file re-adds it; delete again when done)

## Commits on wp/P2-15-impl-2

- `test(frontend): P2-15 spec tests (red)`: T-16 `frontend/src/machines/focusSession.test.ts`,
  T-17 `frontend/src/components/focus/FocusBar.test.tsx` (two tests, both test.fails), stub
  machine `src/machines/focusSession.ts`, `src/test/msw/focus.ts`, Quiet default added to
  `src/test/msw/handlers.ts`. ESLint still flags: no-empty-function x2 in the stub (goes
  away with the real machine) and one no-unnecessary-type-assertion in focusSession.test.ts
  (`kind as never` in `focusEvent`; type the param as the machine's kind instead; this is
  harness code, not an assertion). Run `make check` before the next push.

## State of PR #127

- CodeRabbit: `@coderabbitai review` requested once at open (comment 5925102886); no
  review yet when c1 stopped. Do not request again unless 10 min pass with none.
- CI run 36818030948 (at `ecac044`): contract, daemon, lint, red-proof, security, skills,
  spec-guard, traceability, version-skew PASS; **unit FAIL** (logs not available yet:
  `gh run view 36818030948 --repo SpaceshipCreative/tumnis-guide --log-failed`; locally the
  unit layer was 1697 passed, so look for an env/ordering difference, e.g. the semgrep meta
  test or a test that sweeps routes/ops and now meets `/v1/test/tick/{schedule_name}` or
  the focus routes); integration, e2e, performance pending; preview stays pending.
- Local `make test-int` was used once (c1): all focus tests green (T-08..15); unrelated
  local-only failures: rclone (known), pg_tls (Docker 500), test_attachment_and_nosniff
  (unraisable ResourceWarning). Do not run it again this continuation; use CI.

## Remaining steps

1. PR #127: fix CI `unit` (and anything else red), work CodeRabbit's threads per
   pr-review-loop.md, then send "#127 MERGE-READY at <sha>" to main.
2. impl-2 (frontend), TDD from the red commit:
   - Machine: the full machine is drafted at `/tmp/claude-1002/P2-15-c1/focusSession.ts`
     (the plan's machine verbatim plus `context: { level: "quiet" }`, `SNOOZE_MS`, exported
     types). Replace the stub, unmark T-16 when it passes.
   - `src/components/focus/FocusBar.tsx` (new): `useQuery(focusGetCurrentOptions())`;
     `<section aria-label="Focus">` rendered only when there is a session or an unanswered
     message; title, timer "N min" from `session.started_at`, the message and its `rule`;
     in checkIn: buttons "Still on it", "Switched", "Stuck", "Snooze", "Less of this"
     (min-h-11, wrap at 375 px), posting `POST /focus/respond {event_id, response}` /
     `POST /focus/less {event_id}` through `apiWrite` (`kind: "create"`, schema
     `zFocusCurrentOut`) and putting the answer into the query cache. Drive the machine
     with `useActorRef(focusSession.provide({actions:{postResponse, postLess}}))` (keep
     the latest mutate and event id in refs). Mount it in `components/common/AppShell.tsx`
     between AppHeader/BottomBar and `main`, not on bare pages. Unmark T-17.
   - `src/components/project/FocusCadence.tsx`: drafted at
     `/tmp/claude-1002/P2-15-c1/FocusCadence.tsx`; mount it in
     `components/project/rail/SettingsSection.tsx`; check `useUpdateProject`'s patch type
     takes `focus_cadence_min`.
   - `src/components/dashboard/FocusLevel.tsx`: today override chip (level in force,
     workspace level select via `PUT /focus/level`, "Less today" via `POST /focus/less`).
   - Then `make check`, `npm --prefix frontend run typecheck`, open the impl-2 PR
     ("[P2-15] impl-2: focus bar, level chip and cadence"), request CodeRabbit once.
   - A2.6 (`frontend/e2e/journeys/J8.spec.ts`) keeps `test.fail()` (seed gap below).

## Decisions and deviations (also in PR #127's body)

1. Workflow ids `focus_plan:<ws>:<day>:<plan_id>` (superseded by prefix) and
   `focus_session:<task_id>:<started_at>`.
2. `calendar.synced` not subscribed (nothing moves a published plan's blocks yet).
3. `focus_min_due_seconds` is the module seam `focus.workflows.use(min_due_seconds=)`.
4. `NoulAnswer` is focus's own; Noul fields follow the locked catalogue.
5. A task entering In progress ends other open sessions (one session at a time).
6. Tick registry in `core/ticks.py`; route `POST /v1/test/tick/{schedule_name}`.
7. Workflows never read a clock: time from start instant / message / wait outcome.
8. Harness `_focus.py` settle: a focus workflow counts busy until parked in recv (DBOS
   3.1 `dbos.operation_outputs` sleep count = recv count + 1) with no unconsumed message.
9. `focus_cadence_min` (5..240) on ProjectOut/ProjectPatch; the column already existed.

## Scott items

- A2.6 blocked by the seed gap (seed `Write proposal` plan and runner recording
  `plan__write_proposal_block`).
- T-P2-15-18 waits for #103 (P2-03): then add `focus.responded` to `DIGEST_EVENTS` in
  `agents/rules.py` and unmark.
- New core test route `POST /v1/test/tick/{schedule_name}`.
- Deviations above.

## Verify

- `make check` with `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`,
  `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/P2-15-c2/semgrep/`.
- Unit: `cd backend && uv run pytest -q -m "not integration and not contract" -n 3`.
- Frontend: `cd frontend && npx vitest run src/machines src/components/focus`.
- Integration: CI (`gh pr checks 127`).
