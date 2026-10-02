# P3-09 handoff (Retention and purge), from continuation c1

Written on a HANDOFF NOW from the context watcher. **Delete this file in a `chore:` commit before the PR is marked ready.**

## State

- Branch `wp/P3-09`. Draft PR #169: https://github.com/SpaceshipCreative/tumnis-guide/pull/169. The PR still has its old placeholder body.
- Commits since c0:
  - `af85e86b` merge of origin/wp/P3-09 into the c1 worktree.
  - `646c2705` feat(integrations): P3-09 retention and purge backend (migration, retention engine, api, router, workflows, events, tick, tasks hooks, projects `purge_project(details=)`/`archived_project_ids`, `make gen` output).
  - `a6436c63` refactor(core): audit redaction keeps SENSITIVE_KEY and adds an exact-match allowlist. `NOT_SENSITIVE_KEYS = frozenset({"context_items"})`, plus `core/tests/unit/test_audit_not_sensitive.py`. The coordinator asked for this; the commit cites the note.
  - `4aa0369c` feat(settings): P3-09 retention setting and purge dialogs (RetentionSection, PurgeDialog, ConnectionDetail "Purge content", project rail "Purge project", live-map NOT_LIVE `purgesGetPurge`). Initial JS is 194.3 KB of the 200 KB budget.
  - The handoff commit (this file only).
- Every spec marker is still on. None has been removed.
- CodeRabbit has not been requested. The PR is still a draft.

## CI

**Run 36982365716 (head `a6436c63`): failure.** These jobs failed:

- **unit**: only `test_retention.py::test_cutoff_and_candidates_table`, as XPASS(strict). That is good marker evidence for T-P3-09-01.
- **integration-b**: all six of `test_purge.py` T-02 to T-07 show XPASS(strict), which is good marker evidence. The job also has two real failures:
  - `test_stairway_on_seeded_database` FAILED.
  - 308 setup ERRORs (the `two_workspaces` fixture).
- **integration-a**: one setup ERROR, `search/tests/integration/test_ranking.py::test_results_are_workspace_isolated`.

All of the real failures and errors have the same cause, and **this is our bug, not main's**: `psycopg.errors.CheckViolation: new row for relation "purges" violates check constraint "ck_purges_scope"`. It is raised from `tumnis/core/tests/integration/row_factory.py:245 insert_row`, which `fill_every_table` and `tests/fixtures/__init__.py:327 two_workspaces` call.

The other jobs passed: e2e, contract, lint, security, daemon, performance, red-proof, spec-guard, traceability, skills.

Run 36982166205 (head `646c2705`) was cancelled. The CI run for head `4aa0369c` (the frontend commit) had not finished at handoff; check it with `gh run list --branch wp/P3-09 --workflow ci --limit 3`. Its Vitest job should show T-P3-09-08 as "Expect test to fail", which is the marker evidence for it.

## Remaining steps, in order

1. **Fix row_factory.** Add `purges` overrides to the dict at the top of `backend/tumnis/core/tests/integration/row_factory.py`, next to the other `ck_*` entries. This is a shared test-helper edit; list it under shared-file edits in the PR body.
   - The constraints in `integrations/migrations/0005_purges.py` are:
     - `ck_purges_scope`: scope IN ('retention', 'project', 'connection').
     - `ck_purges_status`: status IN ('queued', 'running', 'done').
     - `ck_purges_target`: `(scope='retention') = (target_id IS NULL)`.
     - `ck_purges_cutoff`: `(scope='retention') = (cutoff IS NOT NULL)`.
   - First check whether `insert_row` fills nullable columns. If it fills them, scope `connection` also needs `cutoff` to be NULL, and scope `retention` needs `target_id` to be NULL. Check how an override of `None` behaves, or whether the factory skips nullable columns.
   - Likely entries: `("purges", "scope"): "connection"` and `("purges", "status"): "done"`, plus whatever makes cutoff NULL. Comment each with its constraint name.
   - Verify with `make check`, then run locally (Docker needs `dangerouslyDisableSandbox`) from `backend/`: `rtk proxy uv run pytest tumnis/core/tests/integration/test_migrations.py tests/isolation -n 3`. Commit as `fix(core): ...` or `test(core): ...`, then push.
2. **Remove the markers, one commit each**, each citing CI run 36982365716 (or the latest run that shows the XPASS). Change only the marker line, never an assertion (decision 78).
   - Remove `@pytest.mark.xfail(strict=True, reason="spec:P3-09")` from T-P3-09-01 in `integrations/tests/unit/test_retention.py`.
   - Remove the same marker from each of T-02 to T-07 in `integrations/tests/integration/test_purge.py`.
   - Change `test.fails(` to `test(` for T-P3-09-08 in `frontend/src/components/settings/retention/Retention.test.tsx`. Wait for a CI run on `4aa0369c` or later that shows it.
   - After that, `make check` must pass. Run it with `bash /tmp/claude-1002/P3-09-c1/check.sh`, which sets the SEMGREP_* environment variables; the log goes to `check.log`.
3. **Migration renumber (wait for the coordinator).** fix/main-followups takes `integrations_0005`. When the coordinator says it has merged:
   - Rename `0005_purges.py` to `0006_purges.py`, with revision `integrations_0006` and down `integrations_0004` changed to down `integrations_0005`.
   - Then `/usr/bin/git merge origin/main`, `make gen`, `make check`, and push.
   - Until then, keep 0005 as it is.
4. **Delete HANDOFF.md** in a `chore:` commit and push.
5. **PR body.** The draft is at `/tmp/claude-1002/P3-09-c1/pr-body.md`.
   - Fill `MARKERS_PLACEHOLDER` with the run ids and commits.
   - Fill `LAYERS_PLACEHOLDER`: unit, integration, contract, Vitest, e2e, bundle.
   - Add the row_factory edit under shared-file edits.
   - Update the migration id if it was renumbered.
   - Then `gh pr edit 169 --body-file ...`, `gh pr ready 169`, and `gh pr comment 169 --body "@coderabbitai review"`, **once only** (the quota is tight).
6. **Review loop** until CI is green and CodeRabbit has no open threads. Ignore `preview`, and don't chase failures main also shows. Then SendMessage to main: `#169 MERGE-READY at <sha>`. Never merge.

## Local results (c1, before the row_factory issue was known)

- **Integration:** with `--runxfail`, all six purge spec tests pass. The related suites gave 135 passed.
- **Backend unit:** 1910 passed, plus the T-01 XPASS.
- **Frontend:** typecheck and lint are clean. Vitest is green except T-08, which reports "Expect test to fail" because it now passes.
- **Local `make check`** fails only on the T-01 XPASS(strict), which is expected.
- **Known environmental issues** (not caused by this branch):
  - T-P0-15-09's hypothesis deadline fails when its file runs alone (main shows this too). It passes in the full suite.
  - Local schemathesis gives "TRACE 404 vs 405" on untouched endpoints.
- **The local full `make test-int` was not run in c1.** The c2 agent may run it once.

## Decisions and design

The design summary is in the PR body draft. These points also matter:

- **Audit.** There is exactly one `data.purged` audit per purge, with the planned counts. The purges row accumulates the actual counts.
- **Batches.** One transaction per batch. A retried step answers -1 once the batch has committed. The kill point is `integrations.purge.batch_<n>.committing`.
- **Retention.** The cutoff counts back from the scheduled time; the test tick passes the test clock's time. Stamps use `fake_scripts.worker_now()` (decision 86). The child `purge_scope` runs directly under `SetWorkflowID("purge:<id>")`, because the maintenance queue has concurrency 1.
- **Test helper.** `_purge.PurgeWorld.outbox` now returns rows `(event_id, payload)`. That is a helper fix; no test body or assertion changed.

## Deviations (also in the PR body draft)

- Index cleanup is the ids-only `items.purged` event.
- The TaskDrawer has no context-item list, so "Removed by retention" exists only in the API and the packet text.
- A connection purge keeps the connection.
- Calendar and knowledge records are not purged.
- Proposals are not held.

## Shared-file edits

- `core/audit.py`: the NOT_SENSITIVE_KEYS allowlist and its test, at the coordinator's request.
- `projects/api.py`
- `tasks/api.py`: the hooks.
- `backend/.importlinter`: the test-only ignores.
- `frontend/src/lib/live-map.ts`
- The project rail `SettingsSection.tsx`
- `settings/sections.ts` and `routes/settings.$section.tsx`
- Generated files.
- Pending: the `row_factory.py` overrides (step 1).

## Scott items

- None.

## Scratch files

All are in `/tmp/claude-1002/P3-09-c1/`:

- `check.sh` (make check with the SEMGREP_* environment variables)
- `wait_ci.sh <sha>` (a bounded CI poll)
- `pr-body.md`
- `msg*.txt`
