# P3-09 handoff (Retention and purge)

Written on a HANDOFF NOW from the context watcher. **Delete this file before the PR is marked ready.**

## State

- Branch `wp/P3-09`. Worktree `/home/claude/Projects/Tumnis-Guide/.claude/worktrees/agent-a2dfc2a098770543c`.
- Draft PR #169: https://github.com/SpaceshipCreative/tumnis-guide/pull/169. It still has the placeholder body (`/tmp/claude-1002/P3-09-c0/pr-body.md`).
- Commits:
  - `cca5a6e1` test(integrations): P3-09 spec tests (red), with the interface stubs. This includes the `.importlinter` test-only ignores, the `ContextItemOut`/`PurgeIn`/`PurgeOut` field additions, the FE stubs and `make gen` output.
  - `1f3ec4b8` merge of origin/main `ea0706b3` (P3-02 merged).
  - The handoff commit (this file only).
- CI: the red commit's CI was not checked before the handoff. Check it with `gh pr checks 169`.
- CodeRabbit: not requested yet. The PR is still a draft.
- Every spec marker is still on. None has been removed.

## Uncommitted work in the worktree (NOT committed, because `make check` has not been run on it)

A backup is at `/tmp/claude-1002/P3-09-c0/wip/`. It holds `tracked.patch` (git diff) and copies of the untracked files.

- `backend/tumnis/modules/integrations/migrations/0005_purges.py`
  - revision `integrations_0005`, down `integrations_0004`, expand.
  - Creates the `purges` table and adds `context_items.target_purged_at`.
- `integrations/models.py`: `Purge` model and `ContextItem.target_purged_at`.
- `integrations/rules.py`: `RetentionSetting`, `IngestedLite`, `purge_cutoff`, `purge_candidates` are implemented. T-P3-09-01 passes locally with `--runxfail`.
- `integrations/payloads.py`:
  - `PurgeRequestedV1` ("purge.requested")
  - `ItemsPurgedV1` ("items.purged")
  - Fixtures are under `backend/tests/contract/fixtures/events/{purge.requested,items.purged}/v1.json`.
  - `make gen` has NOT been run for them yet.
- `projects/api.py`:
  - `purge_project(..., details=None)` merges `details` into the audit details. The `scope` value stays "project".
  - New `archived_project_ids(s)`.
- `integrations/retention.py`: the engine. It is written but not run, and has not been through ruff or mypy.
  - Scope predicates, holds, `project_targets`, `plan`, `run_batch`, `finish`.
  - Hook registries `register_retention_hold` and `register_project_holders`, with the DTOs `LinkRef`, `Holds`, `ProjectHolders`.
  - It uses `blobs.blobs(...)`. Check that function's return shape (core/archive_blobs.py:225) against the `for _ref, raw in ...` unpacking.

## Remaining steps

1. **`integrations/api.py`:**
   - Re-export the retention names.
   - Register the settings section `integrations.retention` (`RetentionSetting`) via `settings_store.register_section`.
   - Write `purge(s, PurgeIn, now=)` for project and connection scope:
     - A blank reason gives 422 and an unknown target gives 404.
     - Insert the purges row with planned counts. Project scope also stores the `targets` snapshot (from `project_targets`), made BEFORE `projects.purge_project(details=...)`, which drops the archive blob.
     - Audit once. Project scope audits through `purge_project`; connection scope through `audit.record` with target ("connection", id). The details hold `purge_id`, `counts` and `scope`.
     - Emit `PurgeRequestedV1`.
     - Return `PurgeOut` with status "accepted" and `purge_id`.
   - Add `get_purge`, returning `PurgeStatusOut{id, scope, target_id, reason, status, counts, created_at, finished_at}`.
   - `context_item_texts`: an item with `target_purged_at` set gives `retention.PURGED_TEXT`.
   - `ContextItemOut.purged` comes from `target_purged_at`.
2. **`router.py`:** route `GET /v1/purges/{purge_id}` (session only). Check that the existing `POST /v1/purges` gives 202 and validates the reason.
3. **`workflows.py`:**
   - Add `PURGE_BATCH = 500` and `PURGE_PAUSE_S = 0.2`. Read them from module globals at call time; the kill-test probe sets them.
   - Step `integrations_purge_batch` calls `retention.run_batch`, and a finish step follows it.
   - Workflow `integrations_purge_scope(ws, purge_id)` loops batches until 0. A result of -1 means already done, so it continues. Between batches it calls `DBOS.sleep_async(PURGE_PAUSE_S)` if the pause is above 0.
   - Workflow `integrations_retention_purge(scheduled_at, context)`:
     - Its start step reads the setting and computes the cutoff. It does nothing if the setting is off, the plan is empty, or a retention purge is unfinished.
     - Otherwise it inserts a purges row with id `uuid5(f"{ws}:{scheduled_at}")` ON CONFLICT DO NOTHING, and records an audit with SYSTEM_ACTOR. The reason is "Retention: content older than {days} days". The details hold scope, purge_id, counts and cutoff.
     - It then runs the child `purge_scope` under `SetWorkflowID(f"purge:{id}")`.
   - Add the schedule entry `"retention-purge"`, `"17 * * * *"`, on MAINTENANCE_QUEUE.
4. **`testing.py`:** register the tick `retention-purge`. **`events.py`:**
   - Re-export the new payloads.
   - Add subscriber `integrations.start_purge` on `purge.requested`. It enqueues `integrations_purge_scope` on "maintenance" as `purge:<id>`, in a fresh `contextvars.Context()` (the pattern of `projects.workflows._enqueue`).
5. **`tasks/api.py`:** register the hooks with integrations.
   - Retention hold: `task_context_items` links and owner_type task. A task is open when status != 'done' and deleted_at is null. A task in a `projects.dormant_projects` project counts as archived.
   - Project holders: `owners={"task": project's task ids}`, and `item_ids` are the `task_context_items` of those tasks.
6. **Run checks and push:**
   - Run `make gen`, then `make check` with these environment variables (the semgrep meta test needs writable paths):
     ```
     SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P3-09-c0/semgrep-settings.yml
     SEMGREP_LOG_FILE=/tmp/claude-1002/P3-09-c0/semgrep.log
     SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P3-09-c0/semgrep-version
     ```
   - Run the purge tests locally with `--runxfail -n 3`: `rtk proxy uv run pytest tumnis/modules/integrations/tests/integration/test_purge.py --runxfail -n 3` from `backend/`.
   - Commit as `feat(integrations): ...`, then push with `/usr/bin/git push origin HEAD:wp/P3-09`.
7. **Frontend:**
   - Build a real `RetentionSection` and add "retention" to `SETTINGS_SECTIONS`/`SECTION_LABELS` and the `settings.$section` route.
   - Build a real `PurgeDialog`: reason required, backup-window text, `POST /v1/purges`, then `onDone`.
   - Add a Purge button to `ConnectionDetail`, and a project purge to the project Settings rail.
   - Check the phone width (375 px) and the 200 KB initial-JS budget.
8. **Markers:** once CI shows XPASS(strict) failures, remove one marker at a time, citing the CI run id in each commit (decision 78). Never change an assertion.
9. **PR:**
   - Delete HANDOFF.md.
   - Write the full PR body: design, per-layer results, shared-file edits, deviations, Context7 citations, Scott items. End it with the Claude Code line.
   - Run `gh pr ready 169`, then comment `@coderabbitai review` once.
   - Run the review loop until CodeRabbit has no open threads and CI is green, then send SendMessage "#169 MERGE-READY at <sha>" to main.

## Decisions and design

- **Audit.** Each purge writes exactly one `data.purged` audit row: in the request transaction for a user purge, and in the retention start step for retention (only when something is purged). The audit counts are the planned counts; the purges row accumulates the actual counts.
- **Batch transactions.** One batch is one transaction: rows, raw payloads (integrations-owned record types only; kept rows lose their pointer), context items marked `target_purged_at`, counts, the batch number, and `items.purged` per type. The kill point `integrations.purge.batch_<n>.committing` fires inside it, before the commit.
- **Type order.** Messages go first, then notes, then threads. A thread goes only once no message references it.
- **Retention holds.** An open task's links are held, and so is an archived project's content: its own live or archived items, and its tasks' items.

## Deviations (for the PR body)

- Nothing indexes messages, threads or notes, so index cleanup is the ids-only `items.purged` event.
- The TaskDrawer lists no context items yet, so only the backend half of "Removed by retention" exists.
- A connection purge keeps the connection, so a later sync may bring its content back.
- Calendar and knowledge records and their raw payloads are not purged (other modules own them).
- Proposals are not held (P3-07 is deferred).

## Shared-file edits so far

- `backend/.importlinter`: two P3-09 test-only ignores (integrations tests -> projects tests; integrations tests -> tasks.api).
- Cross-module edit to `projects/api.py` (see above).

## Scott items

- None yet.
