# HANDOFF · P1-08 Enrichment by the project agent (c1 → c2)

Stopped on "HANDOFF NOW" from the context watcher. Branch `wp/P1-08` (pushed). **No PR yet.**
Original task prompt: `/tmp/claude-1002/coord7/p1-08.txt`. Scratch: `/tmp/claude-1002/P1-08-c1/`
(the new agent uses `$TMPDIR/P1-08-c2/`). A **draft PR body** is at
`/tmp/claude-1002/P1-08-c1/pr-body.md` (copy it; fill `TEST_RESULTS_PLACEHOLDER`; add deviation 9
below).

## Commits on wp/P1-08

- `24100b6` test(agents): P1-08 spec tests (red) (c0)
- `f7f97fb` chore: P1-08 handoff (c0)
- `818ccd0` merge of main (7083652, incl. #104 P2-02) into the WP; conflicts in fake_runner.py,
  agents/api.py, agents/rules.py resolved keeping both sides (strict-mode refusal keeps the gate
  via `dataclasses.replace`)
- `eaa1f02` feat(agents): rules (T-P1-08-01 green, markers removed), `EnrichTask.label` nullable
- `5a4d448` feat(agents): workflow, subscribers, tasks writes, migration tasks_0007, frontend
  (T-P1-08-15/16 green, markers removed)
- this commit: `fix(agents)`: enrichment skips projects whose agent is `not_provisioned`; the 13
  integration markers in `test_enrich.py` removed (see below) + this file

## State

- Unit (`make check` backend part): green except the known local semgrep meta test, which
  passes with `env SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P1-08-c1/semgrep-settings.yml
  SEMGREP_LOG_FILE=... SEMGREP_VERSION_CACHE_PATH=... uv run pytest -q tests/meta/test_security_job.py`.
- Frontend: typecheck, lint and all 183 Vitest tests green (FirstAction + TaskDrawer included).
- spec_guard: "No locked test was weakened".
- `make test-int` (run on `5a4d448`, markers still on): **all 13 test_enrich.py tests
  XPASS(strict)**, i.e. they pass, so their markers were removed in this commit. Other failures
  in that run:
  - `search/.../test_index_events.py::test_task_changes_reach_index_through_events` and
    `tasks/.../test_override_label.py::test_title_edit_relabels_unless_human_chose`: caused by
    this WP. Projects made through the API get a P1-06 profile row that stays
    `provisioning`/`not_provisioned` (no runner); the enrichment then wrote status (version
    bumps → the P1-07 test's PATCH got 409) and a placeholder whose `task.updated` doc is
    stamped with the system clock (newer than the test's FixedClock rename, so search kept the
    old title). **Fix committed here, not yet re-verified**: `enrich_load_step` now returns
    None (writes nothing) when `agent_for_project` says `not_provisioned`.
  - `search/.../test_typeahead.py::test_typeahead_latency_on_load_fixture` (p95 129 ms > 100 ms
    under VM load; unrelated), `test_rclone_copy_keeps_files_removed_at_source` (known local
    timeout), `auth/.../test_lockout.py::test_address_lock_spans_emails` setup ERROR (load; check
    it in CI).
- `A1.1` and `A1.4 step 2` keep their `spec:P1-08` markers (Scott item 2). Never remove them.

## Remaining steps

1. Re-run `make test-int` (bare, from the worktree root; or rely on CI) and confirm the two
   P1-08-caused failures above are gone and test_enrich stays green. If `test_index_events`
   still fails, the cause is the placeholder event's timestamp: consider passing the
   triggering event's `occurred_at` (as `decisions.label_task` does with `as_of`) through
   `enrich_task` to `set_enrichment_status(..., now=...)`.
2. `make check` must pass (only the semgrep env issue locally).
3. Open the PR: `gh pr create --base main --head wp/P1-08 --title "[P1-08] impl: Enrichment by the
   project agent" --body-file <body>`; then `gh pr comment <url> --body "@coderabbitai review"`.
4. Review loop (~/tumnis-coordinator/pr-review-loop.md); when CI green and no open CodeRabbit
   threads, SendMessage to main: "#<PR> MERGE-READY at <sha>". Then delete this file in a chore
   commit.

## Design (as built)

See the draft PR body for the full summary. Key points:
- Status writes bump the version (touch trigger), so each status shares a statement with its
  field write: placeholder + `pending`; `running` before dispatch; apply + `done` (one
  `task_changes` row whose `task_version` is the task's version, so `get_task` returns its
  `change_id` when `enrichment_status == "done"`).
- Apply re-reads, merges (`merge_enrichment`) and writes at the read version, retrying on
  `StaleVersion` (3 attempts); the outlier review item is added in the same transaction when the
  applied estimate is the flagged one.
- Run id `uuid5(ENRICH_RUNS, DBOS.workflow_id)`; child `run_skill` under
  `SetWorkflowID(run_workflow_id(run_id))`.
- Workflow id `enrich:{task_id}:{event_id}`, partition key `str(project_id)` on `runs`.
- `enrich_on_update`: only after an ended enrichment; `only=["estimate_minutes"]` after `done`.
- `TaskOut.first_action_source` / `enrichment_status` are `str | None` (the T-16 spec test uses
  plain string literals).

## Deviations (for the PR body; 1-8 are in the draft)

1-8 as in `/tmp/claude-1002/P1-08-c1/pr-body.md`. Add:
9. **Projects whose agent is not provisioned are skipped entirely** (replaces draft deviation 5):
   no placeholder, no status; `not_provisioned` is written only when the profile disappears
   during the wait. Reason: writing to every task of a project without a working agent bumped
   versions and emitted events that broke P0-20 and P1-07 tests (and would 409 users' edits).

## Scott items

1. Hybrid split location: no description column; built as `AI part:` / `Your part:` lines after
   the acceptance criteria.
2. A1.1 and A1.4 step 2 stay red (seed lacks `Acme site` / `Beta app` projects and agent
   profiles; A1.1 needs a REST-scriptable fake runner in the compose stack).
3. Tasks in a project whose agent is not provisioned get no placeholder and no enrichment later
   (no re-enrichment when provisioning completes). Confirm, or ask for a backfill on
   provisioning.

## Context7 / docs

DBOS 3.1.0 via Context7 `/dbos-inc/dbos-docs`: queue tutorial + queue reference (partition keys
and deduplication ids cannot be combined), workflow tutorial (SetWorkflowID idempotency, child
workflows, `DBOS.sleep_async`), `start_workflow_async` reference. Already cited in the draft.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_missing_fields.py
make check
make test-int      # bare, from the worktree root
npm --prefix frontend run test -- --run src/components/common/FirstAction.test.tsx src/components/project/drawer/TaskDrawer.test.tsx
python3 scripts/ci/spec_guard.py --base origin/main --head HEAD --labels ""
```
