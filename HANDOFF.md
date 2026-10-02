# HANDOFF: JOURNEYS-B (J2 A1.1, J3 A2.1, J8 A2.6)

Instructions: ~/tumnis-coordinator/prompts/wave1/JOURNEYS-B.txt (binding). Branch `fix/journeys-run-focus`.
Push with `/usr/bin/git push origin HEAD:fix/journeys-run-focus`. Draft PR **#161**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/161), based on main 67a43da.

## Commits
- `acb7b85a` fix(frontend): A2.1 review badge and a decision that met only a re-rank
- (this commit) chore: JOURNEYS-B handoff

## State per journey
| Journey | State |
| --- | --- |
| J3 / A2.1 | **GREEN in CI.** Run 36962009623 (e2e job 110697529952) shows "Expected to fail, but passed" on phone and laptop. NEXT STEP: remove the `test.fail();` line in `frontend/e2e/journeys/J3.spec.ts` (line 33). Keep the header comment accurate (decision 78), then commit `test(e2e): A2.1 passes; marker off (CI run 36962009623)`. If the removal is denied, retry once; if it's still denied, report READY EXCEPT MARKERS. |
| J8 / A2.6 | Root cause found, fix NOT written yet (details below). |
| J2 / A1.1 | Product decision received: **coordinator decision 83**, option (b). Not started. |

## J3 fixes (done, in acb7b85a)
- `frontend/e2e/phase2.ts` `reviewBadge` helper: `/^Review/` matched both the rail link and the header bell (strict-mode error). It now names the dashboard's `N to review` badge in `main`. `ReviewBadge.tsx` puts `data-testid="review-count"` on its count, which shows 0 too. The header bell hides its count at 0.
- `ReviewQueue.tsx` `rerankedOnly` + retry: the worker re-ranks open review items (`refresh_review_impact`, `set_review_jev`), and the touch trigger bumps `version` on every write. So Reject soon after load got 409 stale_version (seen on the phone). When the 409's `current` row is still open with the same kind, payload and snooze, the queue sends the decision once more at the current version with a new idempotency key. Test: `ReviewQueue.conflict.test.tsx` (3 cases).

## J8 root cause (fix to write next)
The test clicks Start, then calls `advanceBothClocks(+25)` at once. The `focus-wake` tick (`backend/tumnis/modules/focus/testing.py::wake`) lists only the focus workflows that already exist. The session workflow is started asynchronously: outbox, then relay, then the `focus.track_session` subscriber (deliver_event), then `workflows.start_session` enqueues `focus_session`. So the +25 tick reaches nothing, the session waits a real WAIT_SLICE_S, and no check_in_due appears.

Proof, from the local stack's `dbos.notifications`: the session's only message arrived 13 s later, from the NEXT test's DAY_ONE tick.

Planned fix, agreed with the advisor:
- In `wake()`, mirroring `planning/testing.py`'s planner tick, first settle, bounded at about 5 s and polling every 50-100 ms, until:
  - (a) no outbox row is unsent. outbox is a tenant table with RLS: check `migration_helpers.create_tenant_table`'s policy, then count per workspace inside `tenant_session(WorkspaceContext(ws, SYSTEM_ACTOR))` over `audit.workspace_ids`, as the planner tick does.
  - (b) no ENQUEUED/PENDING `deliver_event` workflow whose id contains `":focus."` (`client.list_workflows_async(name=["deliver_event"], status=[...])`).
- Log if it gives up, then run the existing listing and send loop.
- TDD: first write an integration test in `backend/tumnis/modules/focus/tests/integration/` that moves a task to In progress through the real event path, calls `wake()` immediately, and asserts it reached the new session's workflow. Copy the fixture shape from the existing T-P2-15 tests.
- Timing note: after the tick, the session still waits δ (1-3 s real) because started_at is a few seconds after 10:00. That fits the 5 s expect.
- Follow-up for the PR body only (don't fix it): `/v1/test/reset` doesn't cancel pending focus_plan/focus_session workflows, so they hear later tests' ticks.

## A1.1 plan (coordinator decision 83, Scott may override; name it in the PR body)
- Server side: a dashboard "Just added" list holding the tasks added today by this user that aren't planned or done yet. Newest first, at most 3, hidden when empty, live over /ws. Use a dashboard API addition or a tasks list filter, not client state.
- Rows: TaskRow with LabelChip (`label-chip`, an aria-describedby reason, a menu option "Human"), the FirstAction line (`first-action`, data-state placeholder then agent), and an `estimate-chip`.
- Mount TaskDrawer on the dashboard (`?task=`). Add a History region (from task_changes) to the drawer whose Label entry shows the source "you". The drawer needs the "Acceptance criteria" list.
- Keep A0.1 green: no scroll at 1280x800, and the 375 px variant.
- FIX-app-findings (fix/app-findings-1) may touch TaskDrawer/ReviewQueue for APP-12. Tell main if they conflict.
- A1.1 test steps are in J2.spec.ts lines 216-313. The diagnosis on main: after quick-add, the dashboard shows only toasts, so the label-chip locator finds nothing.

## Local diagnosis setup (decision 76, throwaway, NEVER commit)
- Private stack: `docker compose --env-file /tmp/claude-1002/JB/stack.env -p tumnis-jb -f <worktree>/deploy/compose.test.yaml up -d --wait --build`. The env file sets TUMNIS_TEST_PORT=18983 and TUMNIS_IMAGE=tumnis:jb. Bring it down with `... down -v` when done. It is still up.
- Diag copies (marker line removed, untracked): `frontend/e2e/zz-diag-jb/` (J2/J3/J8 .diag.spec.ts, J8probe, pw.config.ts with baseURL localhost:18983).
  - Run with `<worktree>/frontend/node_modules/.bin/playwright test -c <worktree>/frontend/e2e/zz-diag-jb/pw.config.ts --grep=A2.6`. It needs `dangerouslyDisableSandbox` because the sandbox can't reach localhost; that goes through the permission prompt.
  - Move the folder to /tmp/claude-1002/JB/ before `make check` and back afterwards. Never `git add` it.
- `make check` needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` under /tmp/claude-1002/JB/.
- Host load was 44 on 32 cores: local build_plan's gather_step took 5-8 s, so the planner tick's 8 s budget ran out and local J8 runs failed at the DAY_ONE plan (404). That's the environment, not a bug. CI is the authority.

## CI (run 36962009623, head acb7b85a)
Every job passes except e2e. e2e fails only because J3 is "Expected to fail, but passed" on both projects, which is the marker-removal signal. All other marked journeys fail as expected. preview is pending, as always.

## Denials and Scott items
- One auto-mode classifier denial ([Safety Bypass Flag]) on a plain read-only grep for review-badge usages in frontend/e2e and frontend/src. I didn't retry it, and used the Playwright error output instead.
- No Scott items yet. Decision 83 (A1.1 placement) is the coordinator's, and Scott may override it.
