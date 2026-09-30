# HANDOFF: P2-08 (Taint propagation), continuation c2 next

## c1 status (read first; the c0 notes below are kept for history)

- **PR #114 is open** (https://github.com/SpaceshipCreative/tumnis-guide/pull/114), body from
  `/tmp/claude-1002/P2-08-c1/pr-body.md` (T-01, T-04..07 rows say "pending CI"; update them with
  `gh pr edit 114 --body-file …` once CI shows them). `@coderabbitai review` requested ONCE at
  open; CodeRabbit was still "processing" at handoff (no review, no threads yet). Don't request
  another full review unless it never arrives (pr-review-loop: re-ask after 10 min).
- main merged at 74e5866 (#110, #108); `make gen` produced no drift. No A2.x test belongs to P2-08.
- c1 commits (pushed, head 9ed5189 before this handoff commit):
  - 8922057 `test(tasks): a taint raise keeps the task version (red)`:
    `tasks/tests/integration/test_taint_version.py` (new test, not a spec test).
  - 67bca52 `fix(tasks): … (tasks_0007)`: migration `tasks/migrations/0007_taint_keeps_version.py`,
    revision `tasks_0007`, down `tasks_0006`. `tasks_touch` gets
    `WHEN (NOT (OLD.tainted IS DISTINCT FROM NEW.tainted AND to_jsonb(OLD) - 'tainted' =
    to_jsonb(NEW) - 'tainted'))`; downgrade restores `TOUCH_SQL`. Verified on pgvector:pg18 by
    hand: taint-only UPDATE keeps version; any other or no-op UPDATE bumps. Fixes locked
    T-P0-18-11 (test_board). P2-04 and P1-08 also have tasks_0007: whoever merges later re-chains.
  - 9ed5189 `test(tasks): the taint run helper answers each run once`: ROOT CAUSE of the
    `run_of` 60 s timeouts: `tests/_taint.py::Runs.setup` scripted the fake runner with
    `repeat=10_000`, and `repeat` sends the SAME result 10,000 times inline in the runner's reader
    thread, so a second run's message waited past 60 s (sweep T-07 has one run, so it passed).
    Now `script(PROFILE, SKILL, {"summary": "Done"})` (a script already serves every run).
- CI: the red push's integration job hit its 10-min limit (cancelled; the hang above). The fix
  push (run 36784866876) was pending at handoff. NEXT: read `gh pr checks 114`; for failures
  `gh run view <id> --log-failed` (job logs need `--allow-escape-sequences` and allowed domain
  productionresultssa1.blob.core.windows.net). T-01/T-04/T-05/T-06 + test_comment_taint +
  test_proposal_taint + test_taint_version + test_board must be green in CI (coordinator's rule
  breach repair: never edit assertions, never re-add markers; fix product/harness code). If T-01
  is too slow for the 10-min integration budget, lower `MAX_RUNS`, never `max_examples`.
- **CI on 9ed5189 (run 36784866876), integration: 2 failed, 1179 passed, 21 xfailed in 6m46s.**
  So T-01, T-05, T-06, T-07, test_comment_taint, test_taint_version and the locked test_board
  PASSED in CI (the fixes worked). The 2 failures are a HARNESS bug, not env:
  `test_taint_links.py::test_linking_tainted_item_taints_task_and_unlinking_keeps_it` (T-04) and
  `test_proposal_taint.py::test_proposal_taint_is_the_or_of_its_items`, both at
  `make_world(workspace, clock)`: `FATAL: database "t_…" does not exist`. They request only
  `workspace, clock, db` (no `app`/`dbos`), so `tumnis.core.db`'s engine still points at an
  earlier test's dropped database (`workspace` doesn't call `core_db.configure`; `dbos`, `app`,
  `seed` do, tests/fixtures/__init__.py ~404/453/1061). c0 saw the same locally. Fix in the
  harness, NOT the test signature (adding a fixture to a spec test is a spec change, cf. Scott
  decision 29): e.g. have the `workspace` fixture (tests/fixtures/__init__.py:234) call
  `core_db.configure(app_url=db.app, direct_url=db.app, pooled=False)` like `dbos` does, or an
  autouse fixture in the tasks/integrations integration conftests. Check how
  `tasks/tests/conftest.py` fixtures (make_task etc.) get a configured engine, and mirror it.
  Commit as `test(core)`/`fix(tests)`, push, confirm green in CI.
- CodeRabbit posted its first review at handoff (not yet read): read inline comments, review
  body (outside-diff/nitpicks) and `reviewThreads`, fix or reply+resolve each.
- Then: CodeRabbit loop (pr-review-loop.md), fill the PR body results, delete HANDOFF.md in a
  `chore` commit, push, SendMessage main "#114 MERGE-READY at <sha>".
- Local tooling: `bash /tmp/claude-1002/P2-08-c1/check.sh` runs `make check` with semgrep's
  files under the scratch folder (last run green, 1531 unit passed). No local `make test-int` was
  used in c1 (one allowed).
- Scott items added in c1: deviation 8 (a taint raise changes neither `version` nor
  `updated_at`; it still emits `task.updated` and marks the row changed). Already in the PR body.

---


Branch `wp/P2-08` (pushed to `origin/wp/P2-08` with this commit). Based on main at 5f1e1b3 (#105).
**No PR is open yet.** No CodeRabbit review, no CI run. Scratch folder: `$TMPDIR/P2-08-c1/`
(c0's was `/tmp/claude-1002/P2-08-c0/`: `pr-body.md` is a near-final PR body draft with
placeholders RESULT_0x and LAYERS; `testint1.log` is c0's one `make test-int` run).

## Commits

| SHA | What |
| --- | --- |
| 4f05a9a | `test(tasks): P2-08 spec tests (red)`: T-P2-08-01..08, all strict xfail / `test.fails`; helper `backend/tests/_taint.py` |
| 09853d0 | `feat(tasks)`: rules `TaintSource`, `derive_taint`, `raise_only`, `TaskView`, `may_run_unattended`; T-02, T-03 GREEN, markers off |
| d334043 | `feat(integrations)`: `register_owner_taint` hook in `link_context`; `proposal_taint`; test `test_proposal_taint.py` |
| 7c14062 | `feat(tasks)`: `create_task(context_item_ids=…)` + taint from parent/items/caller; `raise_taint`; `link_context_item` raises; `unlink_context_item`; owner hook registered; `ReviewItemOut.target_tainted`; `make gen` output |
| be95250 | `feat(agents)`: `runs.tainted` from packet in `hermes.py` dispatch (raise-only upsert); `CallerFacts/Caller.run_tainted`, `agent_surface.caller_tainted`, `_taint` kind "run"; `comment_tainted(author, run_tainted=)`, `auth.api.task_token_runs`, packet builder wiring; tests `agents/tests/unit/test_comment_taint.py`, `agents/tests/integration/test_comment_taint.py` |
| 3712b67 | `feat(knowledge)`: `add_document(s, caller, *, project_id, title, body_markdown, tags, now)` |
| ec4f4b3 | `feat(frontend)`: `TaintBadge` on BoardCard, TaskDrawer, ReviewItemCard; T-08 GREEN (`test.fails` -> `test`); extra Vitest for review items |
| (this) | `chore: P2-08 handoff`: ALSO removes the `spec:P2-08` xfail markers from T-01, T-04, T-05, T-06, T-07 (not yet proven green; see below) |

## State of the tests

- Unit layer (`pytest -n 3 -m "not integration and not contract"`): 1531 passed. ruff, format,
  mypy, lint-imports clean. Frontend: typecheck, lint, full Vitest (85 files, 184 tests) green.
- c0's single `make test-int` run (heavy VM load: many Docker 500 "address already in use" and
  "database t_… does not exist" errors = environment, not results):
  - T-P2-08-07 sweep: PASSED.
  - T-P2-08-04: env error (database does not exist). Unproven.
  - T-P2-08-05, T-P2-08-06, `test_comment_taint`: `tests/_taint.py::Runs.run_of` hit its 60 s
    `wait_for(handle.get_result())` timeout. The sweep uses the same helper and passed, so this
    is probably load; check in CI. If CI also times out, compare with
    `agents/tests/integration/test_dispatch_tokens.py` (strict runner, profile "acme-site").
  - T-P2-08-01: Hypothesis `FlakyFailure` rooted in the same `run_of` timeout (_taint.py:114).
  - `test_proposal_taint`: env error (database does not exist).
  - **REAL REGRESSION, must fix:** `tasks/tests/integration/test_board.py::
    test_threshold_change_relays_out_without_losing_data` (locked, P0-24):
    `assert (status, version) == ("in_progress", sub.version)` fails 3 != 2. Cause: linking a
    tainted (url) context item now UPDATEs `tasks.tainted`, and `app.touch_row()` bumps
    `version` on every UPDATE. The test links a url item (through integrations with owner
    `task`, and through `POST /v1/tasks/{id}/context-items`) and expects the version unchanged.
    Do NOT edit that test. Options (pick one, ask the advisor):
    1. A tasks migration (after main's tasks head; P2-04 and P1-08 both add tasks_0007, so
       re-chain as needed) that replaces `tasks_touch` with a trigger that skips when only
       `tainted` changed: `WHEN (NOT (OLD.tainted IS DISTINCT FROM NEW.tainted AND
       to_jsonb(OLD) - 'tainted' = to_jsonb(NEW) - 'tainted'))` plus a second trigger or
       inline `updated_at` handling. Check `tests/meta/_catalog.py:228` (it requires an
       `app.touch_row()` trigger on every tenant table).
    2. A core GUC escape in `app.touch_row()` (`app.keep_version`), set locally by
       `tasks.api.raise_taint` only. Core migration; more invasive.
    A taint raise not bumping `version` is arguably right anyway: it is a system change, and
    live clients refresh through `task.updated` + `mark_changed`, so a user's edit in flight
    doesn't get a spurious 409.
  - Other failures in that run are known env/local ones: test_rclone (known), folder_sync s3,
    sftp service fixture, test_locations errors (Docker 500), test_task_tokens (unraisable
    ResourceWarning from a leftover subprocess).

## Remaining steps

1. Fix the test_board regression (above) with TDD (a new test that a taint raise keeps
   `version`), keeping T-04's stored-taint assertions.
2. Push; open the PR with `$TMPDIR/P2-08-c0/pr-body.md` (fill RESULT_0x/LAYERS; add the
   version decision as a deviation/Scott item; cite SQLAlchemy on_conflict_do_update docs,
   https://docs.sqlalchemy.org/en/20/dialects/postgresql.html, already in the draft).
   `gh pr create --base main --head wp/P2-08 --title "[P2-08] impl: Taint propagation" --body-file …`,
   then `gh pr comment <url> --body "@coderabbitai review"` once.
3. Use CI as the authority for the integration layer (T-01, T-04..07, the two extra
   integration tests). If `run_of` times out in CI too, debug it (see above). If T-01 is
   too slow for the integration budget, lower the number of real runs (`MAX_RUNS`), never
   `max_examples` (the plan says 40).
4. Review loop per `~/tumnis-coordinator/pr-review-loop.md`; then SendMessage to main:
   "#<PR> MERGE-READY at <sha>".

## Decisions and deviations (also in the PR body draft)

1. `runs.tainted = packet.tainted` is written in `DaemonTransport.dispatch` (hermes.py), a
   raise-only upsert, not in `prepare_run` (P2-04 owns it, unmerged). P2-04's `request_run`
   row gains the packet's taint there too.
2. No proposals table (P3-07's); `integrations.api.proposal_taint` is the create-path rule;
   T-01 has no proposal node.
3. T-01 shares one world and runner across examples (the `fake_runner` fixture is bound to
   the `workspace` fixture); fresh rows per example; at most 3 real runs per graph.
4. `TaskView` is a new frozen dataclass (`tainted`, `label`, `status`).
5. Agent-written = `source="agent"` + `created_by` the token; no new column.
6. `context_item_ids` is an `api.create_task` keyword, not on the `create_task` tool input
   (P3-07 adds it for proposal mode).
7. No tainted seed document (it would reach Acme's seeded packets as a passage and could
   change e2e packet previews).
8. No migration so far. Fixing the regression may need one (see option 1).

## Scott items

- Deviations 2 and 7; and whichever version decision the regression fix takes.

## Verify

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy tumnis && uv run lint-imports
cd backend && uv run pytest -q -n 3 -m "not integration and not contract"
npm --prefix frontend run typecheck && npm --prefix frontend run lint && npx --prefix frontend vitest run --maxWorkers=4
make test-int   # bare, from the worktree root, at most once per continuation; CI is the authority
```
