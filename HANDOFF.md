# P2-18 handoff (Project archive and unarchive)

Stopped on the coordinator's HANDOFF NOW. The spec tests are written but only partly checked red. There is no PR yet. Branch `wp/P2-18` is pushed to origin by this handoff commit.

## State

- Commit: `chore: P2-18 handoff`. It holds all work in progress: the spec tests, fixtures, fake-runner support and the zstandard pins. No product code yet.
- Red status:
  - Daemon: 4/4 xfailed (`cd daemon && uv run pytest -q tests/test_archive.py -rx`).
  - Backend integration: not run yet. They need Docker, so run `make test-int` bare from the worktree root. Before the red commit, check that the `archive_world` fixture SETS UP without error (a fixture error is an ERROR, not an xfail).
- Before the red commit, still to do:
  1. Write T-P2-18-09 at `backend/tests/acceptance/test_seed_archive_roundtrip.py::test_seed_project_survives_round_trip`. Use the `seed` fixture plus `ArchiveWorld` (see `_archive.py`) and a protocol-2 `fake_runner` (`runner = fake_runner(profiles=[...], connect=False); runner.homes[name] = {...}; runner.connect_v2()`). Compare tasks, documents, run_events, the folder tree and `runner.homes[profile]` before and after an archive and unarchive round trip.
  2. Add `AuditCase("data.purged", ...)` to `backend/tests/audit_cases.py`. Its perform must archive a project first, then `POST /v1/purges`.
  3. Run `make check` and confirm everything is red. Then drop HANDOFF.md from the tree or keep it updated, and commit `test(projects): P2-18 spec tests (red)`.

## Files so far

- `daemon/tests/conftest.py`: `tmp_profile_home` fixture (`ProfileHome`). It has nested files, an empty file, 0600 `.env`, a 0755 script and a relative symlink. `daemon.toml` also lists the profile.
- `daemon/tests/test_archive.py`: T-01 and T-02, plus two extra tests (active_run refusal, wrong digest).
- `backend/tests/fakes/fake_runner.py`: `connect_v2()`, `homes`, `archived`, `busy`, `home_digest()`, `archives()`, `restores()`, and answers to `archive`, `restore` and `purge_archive`. It imports `ArchiveDone`, `RestoreDone` and `tumnis.core.archive_blobs.Manifest`, none of which exist yet.
- `backend/tumnis/modules/projects/tests/integration/_archive.py`: `ArchiveWorld` helpers. `_archive_probe.py` is the worker-subprocess storage log. `conftest.py` provides the `archive_world` fixture. `test_archive.py` holds T-03 to T-08.
- Shared files: `daemon/pyproject.toml` and `daemon/uv.lock`, `backend/pyproject.toml` and `backend/uv.lock`, each with `zstandard==0.25.0` added (docs: Context7 `/indygreg/python-zstandard`).

## Interfaces the tests lock (implement exactly)

- Daemon, `tumnis_daemon.archive`:
  - `manifest_of(root) -> Manifest`, where `Manifest(entries: tuple[(path, size, sha256)])` has `.digest()`. A symlink is hashed as its link target and never followed.
  - `live_profiles(cfg)`: `cfg.profiles` plus remembered profiles, minus archived ones. An exclusion set is kept in `state_dir`.
  - `async handle_archive(msg, cfg, *, busy=frozenset()) -> ArchiveDone`. It writes `state_dir/archives/<archive_id>.tar.zst`. A busy profile gets `error_code="active_run"`.
  - `async handle_restore(msg, cfg) -> RestoreDone`. It sets `ok=False` on a digest mismatch and keeps the archive file.
  - Use the tarfile `filter="tar"` (checked in the 3.13 source: it keeps modes except the go-w bits, and it blocks writes through links). Validate `archive_id` as a safe filename.
- Daemon `protocol.py`: `ArchiveDone(archive_id, path, size, sha256, manifest_digest, error_code: Literal["active_run", ...] | None = None, error=None)`, `Restore(profile, archive_id, expected_manifest_digest)`, `RestoreDone(archive_id, manifest_digest, ok)` and `PurgeArchive(profile, archive_id)`. Add `"archive"` to `CAPABILITIES`. Replace the `archive_not_supported` case in `main._handle`, and use `live_profiles` in the register.
- Backend `agents/protocol.py`: the same four models at runner v1, added to the unions and the `_DAEMON`/`_SERVER` maps. Add fixtures `backend/tests/contract/fixtures/runner/<name>/v1.json`, then run `make gen` (also needed for the `archive_state` OpenAPI change) and commit what it writes.
- Backend `agents/ws.py`: add `restore` and `purge_archive` to `PROTOCOL_2_COMMANDS`. Handle `archive_done` and `restore_done` like `_provision_result`: correlation_id = workflow id, topic `archive:<archive_id>` / `restore:<archive_id>`, idempotency key = message_id.
- Core: `tumnis/core/archive_blobs.py` holds `Manifest`, the zstd helpers and the put/take/delete functions. Revision `core_0009_archived_blobs` goes after `core_0008_fake_scripts`. Table `archived_blobs(module, kind, project_id, ref, codec, raw_size, stored_size, sha256, data)` is a tenant table, unique on `(workspace_id, module, kind, project_id, ref)`, with `ON CONFLICT DO NOTHING`. Check `tests/meta/test_table_registry.py`.
- Projects:
  - Revision `projects_0002`: `archive_state text NULL CHECK IN ('archiving','archived','unarchiving')`. Add it to `ProjectOut` (the tests read `archive_state` from `GET /v1/projects/{id}`).
  - The routes stay synchronous. Locked T-P0-17-11/12 need `archived_at` set or cleared immediately and exactly one `project.archived` row. The archive route sets `archive_state='archiving'`; unarchive sets `'unarchiving'` when the state is not null.
  - A subscriber in `projects/events.py` starts `archive_project` on `project.archived` and `unarchive_project` on `project.updated` (with `archived_at` changed and archived False). Enqueue it like `agents.workflows.start_provision` (fresh `contextvars.Context()`), with the workflow id taken from the event id.
  - New DBOS queue `archive` in `worker.register_queues`, `partition_concurrency=1`, partition key `archive:<project_id>`.
  - Workflow `archive_project(workspace_id, project_id, actor)`. This deviates from the plan's signature, because the steps need the workspace. Its kill points: `projects.archive.{begin,profile_sent,profile_stored,run_logs,excerpts,folder,finish}`. `finish` emits `project.updated`, never a second `project.archived`, and audits.
  - Hooks go in `projects.api` registries. `agents`, `knowledge` and `integrations` all import `projects`, so `projects` must not import them.
- Agents `agents/archive.py`:
  - Profile hooks: send_archive returns the archive_id, or None when the profile has no runner. The workflow does the recv in its body (R-30) with `WAIT_SLICE_S=3600`.
  - The archive result is stored as an `archived_blobs` row (`kind=profile_archive`), so there is no agents migration (P2-02/P2-03 in flight).
  - Run logs: `run_events` of runs whose profile belongs to the project go to blobs with `module="agents"`, `kind="run_events"`, one step per 500-run batch.
  - Dispatch is refused while the project is archived or has an archive_state.
- Integrations: excerpts are `context_items` with `owner_type='project'`, stored as blobs with `module="integrations"`, `kind="context_items"`.
- Knowledge `knowledge/archive.py`:
  - Tumnis-made folder: tar + zstd the folder into one file OUTSIDE the folder root (e.g. `.tumnis/archives/<folder id>.tar.zst` at the location root), verify the manifest by reading it back, then delete the files. `folder_files` rows are kept. If the pack would exceed 50 MiB (`MAX_FILE_BYTES`), fall back to index-only; this is a deviation and a Scott item.
  - Existing folder: `folder_files` rows and the chunks of the project's documents go to blobs with `module="knowledge"`.
  - `sync._load` must skip the folders of projects whose `archive_state` is not null or that are archived, through `projects.api`.
  - Use `sync.net()` for the net policy.
- Purge: `POST /v1/purges {scope:"project", id, reason}` in `integrations/router.py`.
  - Session only, idempotent write, answers 202. 409 `not_archived`; 422 for a missing or blank reason. It records `audit.record("data.purged", target=("project", id), reason=...)` in the route transaction, soft-deletes the project so GET answers 404, and emits an event.
  - The network and storage deletes (daemon `purge_archive` message, the packed file, the blobs) run in worker subscribers, never in the api process.

## Scott items / deviations to report

- No archive UI exists. The done-checklist line "unarchive from Settings and the archived projects list" is not built, and no spec covers it.
- The 50 MiB pack cap falls back to index-only.
- The routes stay synchronous. `finish_archive` emits `project.updated`, not `project.archived`.
- The workflow signature adds `workspace_id`.
- "Excerpts" are taken to be the project's context_items. Phase 3 will widen this.
- Purge soft-deletes the project and drops its archives. The live tasks and documents rows are left alone.

## Verify

- `cd daemon && uv run pytest -q tests/test_archive.py`
- `make check`
- `make test` and `make test-int`, each bare from the worktree root.
- Push with `/usr/bin/git push origin HEAD:wp/P2-18`. Scratch files go in `$TMPDIR/P2-18-c0/`.
