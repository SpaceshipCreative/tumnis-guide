# HANDOFF: PR #80 (wp/P1-13) reconcile with main after #78 (Scott decision 29)

Stopped on the coordinator's "STOP NOW (VM out of RAM)". Delete this file before merge.

## State
- Branch pushed: wp/P1-13 (from a throwaway worktree branch at main; push with
  `/usr/bin/git push origin HEAD:wp/P1-13`, fast-forward only).
- Commits on top of the old head 11b2a98:
  - bf2b603 `chore(merge)`: merge main 8ffe46a (#78 P1-06, #82, #81, #85/#92, #87, #91, #95)
    into P1-13, reconciling agents per decision 29:
    - `provisioning_failed` registered once (P1-06's). `ProvisioningFailedPayload` is a
      superset: `project_id: UUID` (required; P1-06's workflow now passes `project_id=pid`),
      `mode`, `error` optional, `profile`/`error_code`/`attempt` optional.
    - One subscriber `agents.apply_review_decision` (constant `REVIEW_SUBSCRIBER` in
      agents/events.py) calling P1-06's `retry_provision(project_id, ctx=...)`. #80's
      status-flip `retry_provision` and P1-06's `agents.retry_provisioning` are gone.
    - projects/api.py: #80's duplicate `project_names` dropped (main has it).
    - agents/api.py: `retry_provision` added to `__all__`; stray `tenancy` import removed.
    - frontend: slots.tsx `provisioning_failed` summary falls back to `error_code`
      (TDD: new slots.test.ts, seen red then green).
    - frontend/src/test/msw/handlers.ts: default handlers for `GET */v1/review` (empty
      page), `GET */v1/settings/working-hours` (planning.workingHours()) and a 404
      default for `GET */v1/projects/:projectId`. Needed because main's #76 rule fails any
      unhandled request, and the now-real /review page is reached by locked main tests
      T-P0-22-19, T-P0-23-06, T-DS-01-06 and by T-P1-13-12's `o` (project page). No test
      bodies edited. Same pattern as P1-06's `/v1/agents/profiles` default. MSW docs:
      server.use() handlers prepend and take precedence over setupServer initial handlers
      (mswjs.io/docs/api/setup-server/use, Context7 /websites/mswjs_io).
  - a038168 `test(tasks): T-P1-13-09 uses the dbos fixture (Scott decision 29)`: the spec
    change (dbos param + TYPE_CHECKING import only, no assertion change).
- `make gen`: no changes. `alembic heads`: one head per module (tasks_0005, agents_0003).

## Local results
- `make check` green on bf2b603 (backend unit 1355 passed, Vitest 80 files / 172 tests).
  Needs SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR (read-only ~/.semgrep).
- `make test-int` with a038168's change: 1065 passed, 2 failed, 4 errors. T-P1-13-05..10
  (incl. T-P1-13-09 with dbos) and all P1-06 test_provision tests PASSED. Failures are the
  known VM ones: test_rclone_copy_keeps_files_removed_at_source, test_kill_worker_mid_run_resumes
  (load); errors are Docker 500s (pg_tls, pgbouncer) as the VM ran out of RAM.

## Next steps
1. Update the PR #80 body: scripts are in $TMPDIR/P1-13-reconcile/ (body-orig.md is the
   current body; body_edit.py edits it and inserts reconcile-section.md before
   "## For Scott" -> body-new.md; reconcile-section.md is NOT written yet). Include:
   the reconciliation (above), "Spec change (Scott decision 29)" listing a038168, the
   test results, the MSW defaults + Context7 citation, pydantic optional-field docs, and:
   - **P1-07 note:** `update_task` has no `label_override` on main (P1-07 #93 unmerged), so
     tasks.apply_review_decision does not pass it. P1-07 must add `label_override=False`
     there when it merges.
   - **Migration note for P1-07:** #93 has `tasks_0006` with down_revision `tasks_0004`;
     whichever of #80/#93 merges second must re-chain (tasks_0006 -> tasks_0005).
   - T-P1-13-09 timing risk: with DBOS launched and no runner in the test workspace, the
     enqueued provision_profile ends `no_runner` and flips the profile back to
     `not_provisioned` shortly after; the test reads `provisioning` right after delivery
     and passed locally, but it is a race to watch in CI.
2. Send "#80 needs spec-change label" to main (SendMessage) — NOT yet sent.
3. Watch CI (`gh pr checks 80`), resolve any CodeRabbit threads (pr-review-loop.md); do not
   request a full review. Then delete HANDOFF.md, push, and send "#80 MERGE-READY at <sha>".
