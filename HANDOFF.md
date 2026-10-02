# P4-04 handoff (continuation c2 -> c3)

PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/155 (branch `wp/P4-04`, ready for review).
Push only with `/usr/bin/git push origin HEAD:wp/P4-04`. Scratch: `$TMPDIR/P4-04-c3/`.

## State at handoff

- Head before this commit: `7de3f6d4`. CI run 36974768846 at that head: everything green except
  - **integration-b**: `tests/acceptance/test_a0_3_tenant_isolation.py::test_workspace_b_cannot_touch_workspace_a[session-PUT /v1/unattended/window]` -> `AssertionError: session PUT /v1/unattended/window {}: 422` (the `{}` is `request.params`, not the body).
  - **e2e**: J6 phone failed on `POST /v1/test/reset` 409 "superseded by a later reset" (issue #56 flake pattern).
- CodeRabbit: both inline comments fixed (6607c876, 7de3f6d4), nitpick taken (7de3f6d4); threads replied to and resolved; re-review requested once after the fixes. The CodeRabbit check showed "Review completed". **Check for new comments from that second review** before declaring merge-ready.
- PR body: `/tmp/claude-1002/P4-04-c2/pr-body.md` (applied via `gh pr edit`). Copy it to your own scratch folder before editing.

## Diagnosis of the integration-b failure (not yet fixed)

The sweep's `_body()` (tests/acceptance/_isolation.py:283) builds a random `UnattendedWindowIn` with polyfactory and points `project_id` at A's project, so the request is kind "row" and must answer **404**. `put_unattended_window` (backend/tumnis/modules/planning/api.py ~2002) runs its two 422 checks (repeated weekday; start == end) **before** `projects.get_project(s, body.project_id)`. Polyfactory sometimes generates duplicate weekdays, so the route answers 422 first. That is why it passed on earlier runs (random).

Fix (P4-04-owned code, do not touch the locked A0.3 test): in `put_unattended_window`, open the session and look up `body.project_id` (404 for another workspace's project) **before** the weekday/start==end validation. Optionally add an integration test: a PUT naming another workspace's project with duplicate weekdays answers 404 (needs two workspaces; or skip and rely on the sweep). Update the docstring.

## Remaining steps

1. Apply the fix above, `make check` (SEMGREP_SETTINGS_FILE / SEMGREP_LOG_FILE / SEMGREP_VERSION_CACHE_PATH under your scratch folder), commit `fix(planning): the window PUT answers 404 for another workspace's project before validating (P4-04)`, push.
2. Wait for CI (non-preview checks, hard deadline; `/tmp/claude-1002/P4-04-c2/waitci.sh` is a template). If e2e fails again on the reset 409 flake, `gh run rerun <id> --failed` once.
3. Check CodeRabbit for new threads after the push; address/reply/resolve. Re-request a review only after pushing fixes.
4. Update the PR body: add the `task_done` refusal (6607c876), the per-workspace/per-task try/except guards in `unattended_tick_for` and `release_overnight` (7de3f6d4), and this 404-before-422 fix.
5. When CI is green (excluding preview) with no open threads: SendMessage to `main`: `#155 MERGE-READY at <sha>`.
6. Delete this HANDOFF.md in a `chore: remove the P4-04 handoff` commit.

## Commits on the branch (oldest last)

7de3f6d4 fix(planning,notifications): one failing workspace or task never holds up the others (review)
6607c876 fix(tasks): a done task cannot be queued to run unattended (review)
b881022f chore: remove the P4-04 handoff
69217dbd test: unmark A4.3 green-light (CI run 36972030511)
691e216c Merge origin/main (#154)
7fd36f09 test: A4.3 helper waits for queued event deliveries to settle
eead201d test(frontend): unmark T-P4-04-12 (CI run 36967505368)
2f7bb1d9 Merge origin/main (#152, #160)
2a06af1e style: format the migration
a3a0713b fix: integer weekdays, row_factory values, live map, release-rule unit tests
7ef96b09 test: unmark the unattended integration tests (CI run 36960927427)
9f9bcee4 feat(frontend): unattended UI
e825a40a, 2af0476a, aa598443, 7336850f (unmark T-P4-04-01..04, run 36959204086), 24191dec, c57d6cee, 821d7acf, 57c56dc6, 8e3977d2, 55489cd8, a83f2792, 8bf165b7, df2fe5a8 (spec tests, red)

## Migrations

tasks_0010 (down tasks_0009), planning_0004 (down planning_0003), notifications_0003 (down notifications_0002).

## Shared-file edits

agents/api.py + agents/review_kinds.py (additive result batch hook), core/tests/integration/row_factory.py (COLUMN_VALUES for unattended_windows), frontend/src/lib/live-map.ts, the frontend files listed in the PR body, generated openapi/sdk/mcp tools.

## Deviations

The 11 listed in the PR body (A4.3 written by P4-04; planning's `unattended_runs` table; additive agents edit; `overnight` notifications decision; schedules via `workflows.schedules()`; release clamps to window end; request_run refusals become `unattended_refused` items; window PUT version semantics; no kill-and-resume test; `integer[]` weekdays; A4.3 `fire()` settle wait). Plus, from review: done tasks answer 422 `task_done` on queue; tick and release loops log and continue per workspace/task.

## Scott items

- Done-checklist item "A real overnight run on the homelab lands in the morning review" needs the homelab runner.

## Verify commands

- `cd backend && uv run pytest -q tumnis/modules/planning/tests/unit tumnis/modules/tasks/tests -k unattended`
- Docker-backed (sandbox disabled): `uv run pytest -q "tests/acceptance/test_a0_3_tenant_isolation.py" -k "unattended"` and `tests/acceptance/test_a4_3_unattended.py`
- `gh pr checks 155`
