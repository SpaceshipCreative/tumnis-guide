# P1-11 handoff (Daily plan), continuation c3 -> c4

Branch: `wp/P1-11`. Push with `/usr/bin/git push origin HEAD:wp/P1-11` (never git switch, reset --hard,
symbolic-ref; use /usr/bin/git). **PR #119** https://github.com/SpaceshipCreative/tumnis-guide/pull/119
("[P1-11] impl: Daily plan"), one PR holding backend and Today panel UI (deviation 1). No impl-2 branch.

PR body source: `/tmp/claude-1002/P1-11-c3/pr-body.md` (current; update with
`gh pr edit 119 --body-file <file>`). Original task prompt: `/tmp/claude-1002/coord7/p1-11.txt`.

## Commits in c3 (on top of c2's b4676c6)

- 4dd6e59 fix(core): `TumnisRoute` answers 405 when a templated route matched a path a concrete route
  owns and that route lacks the method (`_check_concrete_path`, `_concrete_methods` in
  `core/routing.py`); fixes schemathesis UnsupportedMethodResponse on `GET /v1/plan/replan`.
  Test `core/tests/unit/test_concrete_path_first.py` (red first).
- 416c531 chore: removed the c2 handoff.
- 5dca3b7 merge origin/main (#123 trivy libpcre2 fix, eb363ad).
- 11ced9a fix(core): `dbos_sys_db` fixture (tests/fixtures/__init__.py) runs
  `run_dbos_database_migrations` once per xdist worker, so `POST /v1/plan/replan` in the A0.3 sweep
  enqueues through the api's DBOSClient (which never migrates). Reproduced red alone, green after.
- 279b110 merge origin/main (#121 P2-05 PR1, 8b24efc). Conflicts: `agents/api.py` `__all__` (kept both
  sides, sorted); generated files taken from main then `make gen` (after `npm ci` in frontend).
  make check green (Vitest 93 files / 204 tests).
- this commit: chore: P1-11 handoff.

## CI state

- 416c531: all required green after one `performance` re-run (board-page Lighthouse TBT flake;
  coordinator approved one re-run, never touch Lighthouse config/budgets or board code).
- 5dca3b7: security green (trivy fixed); integration failed: A0.3 replan (fixed in 11ced9a) and
  `tests/integration/test_taint_propagation.py::test_taint_flows_through_random_creation_graphs`
  (P2-08, Hypothesis FlakyFailure "random module inside strategies"; not ours; passed elsewhere).
- 11ced9a: everything green except e2e: `[phone] e2e/board.spec.ts` T-P0-24-05 keyboard drag
  (P0-24 board, flaky; passed on 416c531 and 5dca3b7). Run superseded by the 279b110 merge.
- 279b110 / handoff commit: CI just started; not yet read.

## CodeRabbit

No unresolved threads (the c2 HANDOFF.md thread was fixed, replied and resolved). It auto-reviews
each push; check `gh api graphql` reviewThreads (isResolved=false) after CI.

## Next steps

1. Delete this HANDOFF.md in a `chore:` commit and push (that starts the CI run that counts).
2. Wait for CI on that head (`gh pr checks 119`; `preview` stays pending, expected).
   - If e2e T-P0-24-05 or the taint-propagation test or `performance` board-page TBT fails, re-run
     that job ONCE (`gh run rerun <run> --job <job-id>`, only after the whole run completes).
   - A real failure: fix it, or report it to main (coordinator wants only MERGE-READY or real failures).
3. Check CodeRabbit threads; handle per pr-review-loop.md.
4. When every required check is green and no open threads: SendMessage to "main":
   `#119 MERGE-READY at <sha>`.

## Verify commands

- make check: `bash /tmp/claude-1002/P1-11-c3/check.sh` (edit its worktree path if yours differs;
  sets SEMGREP_* files in the scratch folder). frontend/node_modules is installed in this worktree.
- Docker-backed focused runs need the sandbox off (bare): e.g.
  `cd backend && uv run pytest 'tests/acceptance/test_a0_3_tenant_isolation.py::test_workspace_b_cannot_touch_workspace_a[session-POST /v1/plan/replan]' -q -p no:randomly`
  and `uv run pytest tests/contract/test_openapi_fuzz.py -q -p no:randomly`.
- CI logs: `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs`
  with allowed domain `*.blob.core.windows.net`.

## Deviations (all in the PR body)

1. One PR instead of impl + impl-2 (classifier denied the plumbing to split).
2. Migration `planning_0003` (chain already existed).
3. Deterministic workflow ids, not a DBOS dedup id (DBOS 3.1.0 + partitioned queue).
4. `build_plan(..., now)`.
5. Master readiness from runner status + profile inventory.
6. `publish_plan` moves `built_at` just past the day's latest plan (T-13).
7. Empty plan emits no `plan.published` (Scott item).
8. Every build floored at now.
9. `PlanIssueOut.offer` nested.
10. Core concrete-path-first: `_allowed_methods` (core/errors.py) and `_check_concrete_path`
    (core/routing.py), OpenAPI 3.1 Paths Object; plan-issue routes use `plan_issue_id`.
11. `PlanContext.ahead`; 12. spec-file fixes at the red commit (no assertion touched).
Shared-file edits added in c3: `core/routing.py`, `tests/fixtures/__init__.py` (both in the PR body).

## Scott items

1. `plan.published` only for non-empty plans (deviation 7).
2. A1.2/A1.3 Playwright stay red: the e2e stack has no scriptable fake runner or tick route.
3. Not provable here: kill test 20 runs in a row; the real master on the homelab.
4. One PR instead of the planned split (deviation 1).
