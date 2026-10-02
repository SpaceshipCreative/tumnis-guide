# P3-09 handoff (Retention and purge), from continuation c2

Written on a HANDOFF NOW from the context watcher. **Delete this file in a `chore:` commit before you send MERGE-READY.**

## State

- PR #169, https://github.com/SpaceshipCreative/tumnis-guide/pull/169. It is ready for review (not a draft), and its body is current (source: `/tmp/claude-1002/P3-09-c2/pr-body.md`).
- Head before this handoff commit: `6b52372d`. **CI run 37004124172 on 6b52372d is all green**, after one re-run of `performance` for dashboard and board TBT (206 and 211 ms against the 200 ms budget; the initial JS is within 0.1 KB of main's).
- The migration is `integrations_0006` (`0006_purges.py`), down `integrations_0005` (#172). `alembic heads` shows one integrations head.
- All 8 spec markers are off: T-01 to T-07 cite CI run 36982365716, and T-08 cites run 36985544838.
- CodeRabbit:
  - It was requested once, when the PR was marked ready. **Do not request it again.**
  - Both inline threads are fixed, replied to and resolved. CodeRabbit confirmed the fixes in replies 4165040864 and 4165044293.
  - The outside-diff finding is fixed (e1f5b0c5, 8146c8d2), with a PR comment.
  - The nitpick (`holds` per batch) is declined, with the reasons in a PR comment.
  - Unresolved threads: 0. CodeRabbit has posted no review since a3bd2600; its hourly quota was nearly used up.

## c2 commits (newest last; merges of main left out)

- The first merge of wp/P3-09: the SettingsSection conflict, keeping both ArchiveControl and ProjectPurge.
- row_factory: `purges` gets `scope=retention` and a cutoff (it fixed 308 `two_workspaces` setup errors).
- 7 unmark commits (integrations), 1 frontend unmark (83a991e3), the HANDOFF removal (cd80f163), and `RetentionRoute.test.tsx` (556a0841, for the "375 px" done-checklist item).
- The renumber to `integrations_0006`.
- e17b61d3 (red), then 5871a3f8 and 5f156a79: a retention purge whose workflow failed for good is forked (`purge:<id>:retry:<scheduled time>`, maintenance queue, current app version) on the next run. It is forked, not resumed, because a resume replays the recorded step error.
- 91c4aa1d: `purge_scope` loops until a batch answers 0 (`MAX_PURGE_BATCHES` is removed).
- e1f5b0c5 (red), then 8146c8d2: a project purge keeps records that another archived project's archive blobs link.

## Remaining steps

1. `/usr/bin/git fetch origin`, then `/usr/bin/git merge origin/main`. #174 (bbfd3600) merged after 6b52372d.
2. Run `make gen` (expect no diff), then `bash /tmp/claude-1002/P3-09-c2/check.sh`. Copy the script to your own folder and fix the worktree path; it writes `check.log`.
3. Delete this file in a `chore:` commit, then `/usr/bin/git push origin HEAD:wp/P3-09`.
4. Wait for CI on the new head (`wait_ci.sh <sha>`; ignore `preview`).
   - Allowed once-only re-runs: a dashboard or board TBT failure in `performance`, J1/A1.2 in e2e (main fails it too, e.g. run 36995287794), and `test_relay` worker-kill in integration-b (main run 36997230716).
5. If green and still 0 unresolved threads, send `#169 MERGE-READY at <sha>` to main.

## Deviations (all in the PR body, with evidence)

- **Search and embedding cleanup** is the ids-only `items.purged` event. `search_index` only admits task and project; embeddings cover knowledge chunks only.
- **"Removed by retention"** shows in the packet and API text (and in GuardrailDashboard). The TaskDrawer has no context-item list.
- **A connection purge keeps the connection.**
- **Calendar and knowledge records are not purged.** The plan lists messages, threads and notes only.
- **Proposals are not held** (P3-07 is deferred).
- **Test helper:** `PurgeWorld.outbox` returns rows.
- **Retention cutoff:** counted from the scheduled time; stamps use `worker_now()` (decision 86).

## Shared-file edits

- `core/audit.py` (the NOT_SENSITIVE_KEYS allowlist, at the coordinator's request), and `core/tests/integration/row_factory.py`.
- `projects/api.py` and `tasks/api.py` (hooks), `.importlinter`.
- `live-map.ts`, the project rail `SettingsSection.tsx`, `settings/sections.ts`, `routes/settings.$section.tsx`, `settings/queries.ts`.
- Generated files.

## Scott items

- None.

## Scratch

`/tmp/claude-1002/P3-09-c2/`: `check.sh`, `wait_ci.sh`, `wait_job.sh`, `waitfile.sh`, `pr-body.md` and the review replies.
