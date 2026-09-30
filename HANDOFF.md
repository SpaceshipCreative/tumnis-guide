# HANDOFF: P2-08 (Taint propagation), continuation c1 next

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
