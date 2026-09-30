# P2-18 handoff 2 (Project archive and unarchive)

Stopped on the context watcher's HANDOFF NOW. There is no PR yet. The branch `wp/P2-18` on origin holds everything listed below (this handoff commit included).

## Commits (on top of main 9b35155)

- `a64d45c` test(projects): P2-18 spec tests (red): T-01..09, the `data.purged` AuditCase, and `.importlinter` ignores for `projects.tests -> knowledge.**` (two contracts).
- `10496f3` feat(projects): the archive_project, unarchive_project and purge backend.
- `203fe03` feat(agents): runner archive messages and socket hand-over (a subagent's work): protocol models, ws `_archive_answer`, fixtures, runner schemas.
- `5f39cf5` feat(daemon): archive, restore and purge profiles. The 4 daemon spec tests pass, and their markers are removed.
- `74d254b` fix(knowledge): a folder that was never made packs as empty; the archives folder is made first.
- `34b67c7` chore(gen): `make gen`, which produced openapi (`archive_state`, `POST /v1/purges`), the frontend client, `schemas/events/v1/project.purged.json` and `test_schema_events.py`.

## State of the tests

- Daemon: 63 passed. The markers on T-01/T-02 and the two extra archive tests are removed.
- Backend unit (`-m "not integration and not contract"`): 1461 passed, as of `10496f3`.
- `make test` (the subagent's run): 2 failures are ours and still OPEN, because the event fixture `backend/tests/contract/fixtures/events/project.purged/v1.json` is missing. Add it by copying the shape of `fixtures/events/project.archived/v1.json`. The failing tests are `test_versions::test_every_schema_has_fixtures_and_upgraders` and `test_every_payload_accepts_n_and_n_minus_1[events/project.purged]`. The other 2 failures are environmental: semgrep on `~/.semgrep` (set `SEMGREP_*` env to `$TMPDIR` as vm-agent-rules says) and promtool.
- `make test-int`: the first green-tree run was INCONCLUSIVE. Docker answered 500 on a container start under VM load (exit 2). Nothing about the archive tests is known yet. The backend markers on T-03..T-09 are still `xfail(strict)`: a pass shows up as XPASS(strict), and that is the signal to remove that marker.
- `make check`: lint, format, mypy and lint-imports are clean. Unit is green as of `10496f3`, apart from the semgrep sandbox issue. Helper script: `/tmp/claude-1002/P2-18-c1/check.sh` sets the semgrep env and writes `/tmp/claude-1002/P2-18-c1/check.log`.

## Next steps

1. Add the `project.purged` event fixture (see above). Run `make check`, then `make test`.
2. Run `make test-int` bare. Expect to debug T-03..T-09 and the `data.purged` audit case (T-P0-15-07). Things to watch:
   - `knowledge/archive.py`:
     - `_tree` catches `storage.NotFound` for a folder that was never made. Check which error ServerPathStorage actually raises.
     - `restore_rows` uses `jsonb_populate_recordset`. Array columns (`chunks.heading_path`) and `OVERRIDING SYSTEM VALUE` are untested.
   - T-08 (kill test): the worker subprocess relays `project.archived`, so a second `archive_project` starts under another workflow id. Both run on the `archive` queue (global `concurrency=1`; DBOS refuses an unkeyed enqueue on a partitioned queue, which is why the queue isn't partitioned). The second finds the state `archived` and returns `skipped`.
   - T-09: the seed workspace goes through a hand-built `WorkspaceHandle`. If seed events relayed during `archive` touch documents, compare that test's reads with the plan.
3. Remove each backend xfail marker once its test passes (Scott approved this).
4. Open the PR. Push `/usr/bin/git push origin HEAD:wp/P2-18`, then run `gh pr create --base main --head wp/P2-18 --title "[P2-18] impl: Project archive and unarchive" --body-file <file>`, then comment `@coderabbitai review` once. The body lists docs, deviations and Scott items (below).
5. Work the review loop (`~/tumnis-coordinator/pr-review-loop.md`). When CI is green and no threads are open, SendMessage "#<PR> MERGE-READY at <sha>" to main.
6. Delete HANDOFF.md in a chore commit.

## Design (as built)

- `core/archive_blobs.py` + `core_0009_archived_blobs` (after `core_0008_fake_scripts`):
  - The tenant table is `archived_blobs(module, kind, project_id, ref, codec, raw_size, stored_size, sha256, data)`, unique on `(workspace_id, module, kind, project_id, ref)`.
  - `Manifest` has a canonical-JSON digest; the daemon keeps an identical copy.
  - `snapshot_rows` (`to_jsonb`) and `restore_rows` (`jsonb_populate_recordset`, generated columns skipped) handle the rows.
  - put/blobs/delete handle the blobs.
- `projects_0002`: `archive_state` NULL | `archiving` | `archived` | `unarchiving`.
  - Archive route: `archived_at` and `archiving`.
  - Unarchive route: `unarchiving` when a state is set.
  - `projects.api`: `register_archive_hook`/`archive_hook`, `archive_facts`, `dormant_projects`, `move_archive_state`, `purge_project`.
- `projects/events.py` subscribers: `projects.start_archive` (on `project.archived`), `projects.start_unarchive` (on the `project.updated` with `archived_at` changed and archived false) and `projects.start_purge` (on the new `project.purged`). They lazily import `projects/workflows.py`, which enqueues on the `archive` queue with workflow id `<name>:<event id>`.
- `projects/workflows.py`: `archive_project(workspace_id, project_id, actor)`, `unarchive_project`, `purge_project_archive`.
  - Kill points `projects.archive.{begin,profile_sent,profile_stored,run_logs,excerpts,folder,finish}`.
  - The `recv` loops in the workflow body with `WAIT_SLICE_S=3600`.
  - Each step no-ops once the project has left the state it expects.
- Hooks:
  - `agents/archive.py`: `send_archive`/`store_archive`/`send_restore`/`finish_restore`, run logs in 500-run blobs, `purge_archive` (sends `purge_archive`), and `dispatch_allowed`, which `dispatch_step` uses to refuse "project_archived".
  - `integrations/archive.py`: `context_items` excerpts.
  - `knowledge/archive.py`: a Tumnis-made folder is packed to `.tumnis/archives/<folder id>.tar.zst` at the location root, verified, then its files deleted. `folder_files` rows are kept. An existing folder, or a pack over 50 MiB, archives index-only (`folder_files` and chunks go to blobs).
  - Each is registered by importing it from the module's `workflows.py`. `ws.py` imports `agents.archive` too, so the hooks also register in the api process, which is harmless.
- `sync._load` skips dormant projects' folders and their records.
- `worker.py`: the `archive` queue, with `concurrency=1`, is in `register_queues` and `main_queues`.
- Purge: `POST /v1/purges` in `integrations/router.py` (session only, idempotent, 202), through `integrations.api.purge` → `projects.api.purge_project`.
  - 409 `not_archived` when `archived_at` is null or the state is `unarchiving`.
  - The `data.purged` audit row carries the reason; the project is soft-deleted and emits `project.purged`.
  - The worker drops the blobs and the pack, and sends the daemon's `purge_archive`.

## Deviations / Scott items (for the PR body)

- No archive UI. The done-checklist line "unarchive from Settings and the archived projects list" is not built, and no spec covers it.
- The `archive` queue is used instead of the plan's `maintenance`, because T-08 locks the name. It is global `concurrency=1`, not partitioned, because DBOS 3.1 requires a partition key on every enqueue to a partitioned queue and T-08 enqueues without one.
- The workflow signature adds `workspace_id`, and the routes stay synchronous. `finish` emits `project.updated`, not a second `project.archived`, and writes no audit row: the plan's "audit" is left for the purge only.
- A pack over 50 MiB falls back to index-only.
- "Excerpts" means the project's `context_items`.
- Purge soft-deletes the project and drops its archives. Live tasks and documents rows are left alone.
- An unarchive whose profile restore doesn't match the manifest fails the workflow, and the project stays `unarchiving`.
- The daemon's `busy` is taken when the archive command arrives. The server-side guard is `dispatch_allowed`.
- Shared-file edits:
  - `backend/pyproject.toml`, `backend/uv.lock`, `daemon/pyproject.toml`, `daemon/uv.lock`: `zstandard==0.25.0`.
  - `backend/.importlinter`: projects.tests → knowledge ignores.
  - `backend/tests/audit_cases.py`: the `data.purged` case.
- Revisions: `core_0009_archived_blobs` and `projects_0002`.
- Docs:
  - Context7 `/indygreg/python-zstandard` (0.25.0: `ZstdCompressor.compress`/`stream_writer`, `ZstdDecompressor.decompress`/`stream_reader`).
  - Context7 `/dbos-inc/dbos-transact-py` (partitioned queues need `queue_partition_key`; `EnqueueOptions`).
  - https://docs.python.org/3.13/library/tarfile.html (extraction filters, `tar_filter`, `TarInfo.replace`).

## Verify

- `cd daemon && uv run pytest -q`
- `/tmp/claude-1002/P2-18-c1/check.sh`, then read `/tmp/claude-1002/P2-18-c1/check.log`
- `make test` and `make test-int`, each run bare from the worktree root.
- Scratch files go in `/tmp/claude-1002/P2-18-c2/`.
