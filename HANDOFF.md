# P1-15 handoff (Project folders and the sync engine)

Stopped on the coordinator's "HANDOFF NOW". Branch `wp/P1-15` (pushed with
`/usr/bin/git push origin HEAD:wp/P1-15`). No PR yet, so there are no review threads and no CI
runs.

## State

| Commit | What |
| --- | --- |
| `9066bad` | `test(knowledge): P1-15 spec tests (red)`: all 13 spec tests as `xfail(strict=True, reason="spec:P1-15")` |
| (this one) | `chore: P1-15 handoff` |

Verified on `9066bad`: `make check` is green (1018 passed, 60 xfailed; the frontend was skipped
because node_modules is absent, and the WP has no frontend change). The unit spec tests are red
(60 xfailed), and the 29 integration spec tests collect. The integration layer has not been run
yet.

Nothing is implemented yet: no `sync_rules.py`, `sync.py`, migration, workflow, api additions,
CLI command or `scripts/backup/folders.sh`.

## Spec tests written (all red)

- Unit: `knowledge/tests/unit/test_decide_sync_action.py` (T-01 with 21 rows; T-02 and T-03 use
  Hypothesis), `test_conflict_name.py` (T-04), `test_filenames.py` (T-05, T-06),
  `test_pair_renames.py` (T-07), and `scripts/backup/tests/test_folders_job.py` (T-12). T-12 is
  collected through the new pyproject testpath `../scripts/backup/tests`.
- Integration:
  - `test_folder_sync_fixtures.py` (T-08: 15 YAML scenarios in `backend/fixtures/folder_sync/`,
    26 cases across the backends)
  - `test_folder_sync.py` (T-09 kill test, T-10 versions)
  - `test_backup_sources.py` (T-11)
  - `test_project_folder.py` (T-13)
- Helpers:
  - `tests/integration/_folder_runner.py`: the scenario runner and its op vocabulary.
  - `_sync_probe.py`: imported by the kill-test worker. It swaps every location for a counting
    `ServerPathStorage` whose write log survives the kill.
- mypy: the tests carry `# type: ignore[attr-defined|import-untyped]` on the imports of names that
  don't exist yet. `warn_unused_ignores` flags each one once its name exists, so delete them as
  you implement.

## Interfaces the tests expect (implement exactly these)

### `knowledge/sync_rules.py` (pure; follow the plan's interfaces)

- The plan's `Prev`, `Remote`, `Local`, `Action`, `SyncDecision`, `decide_sync_action`,
  `conflict_name`, `sanitize_filename`, `dedupe_name`, `pair_renames` and `read_tumnis_id`.
- Also add `render_note(document_id, body)` and a parser for the note body (see Decisions).

Decision rules the rows lock:

- `remote_changed`: if `remote.content_hash` is not None, it means
  `remote.content_hash != prev.content_hash`. Otherwise it means `remote.etag != prev.etag`.
- `meta_only`: not changed, but size, mtime or etag differ.
- `local_changed`: `local.version > (prev.synced_version or 0)` and
  `local.content_hash != prev.content_hash` (and the doc is not trashed).
- A path counts as external when prev.origin or local.origin is "external".
- Row 8 (WRITE_THROUGH): `if_match = remote.etag`. The row test sets it equal to prev.etag.
  T-03 requires `if_match == remote.etag` for WRITE_THROUGH, MOVE_TO_TUMNIS_TRASH and
  DELETE_AT_SOURCE.
- Rows 14 and 16 carry `if_match = remote.etag`.
- Conflicts: row 10 sets `if_match = remote.etag` and `conflict_path`. Rows 4, 9 and 11 set
  `if_match=None` and `conflict_path`, with review `sync_conflict`.
- External, missing and edited in Tumnis: TRASH_DOCUMENT with review
  `deleted_outside_edited_inside`. REWRITE_FROM_TUMNIS is not allowed for external files.
- prev present, remote present, local None: CREATE_DOCUMENT if the remote changed, else NOOP.
- prev None, remote present, local trashed: treat it like row 1.
- Every action that writes nothing has `if_match=None` and `conflict_path=None` (T-03).
- `conflict_name`: siblings are compared casefolded. The marker goes before the last suffix.
  Dotfiles and names without a suffix get it at the end. Collisions get ` 2`, ` 3`.
- `sanitize_filename`:
  - Same rules as P1-16's `rules.upload_file_name`, but with no basename split: '/' becomes '-'.
  - A leading '~' becomes '-'.
  - Reserved names are checked before the first dot (`COM1.tar.gz` gives `COM1_.tar.gz`).
  - The name is cut to 200 bytes keeping the extension, and must pass `safe_rel_path` as-is.
- `dedupe_name`: takes a path, dedupes its last segment casefolded, and keeps the folders.

### `knowledge/sync.py` (new engine module; imports `knowledge.api`)

- `configure(net: NetPolicy | None)`: None falls back to `Settings().net_policy()`.
- `use(clock=..., extraction=...) -> (prev_clock, prev_extraction)`: the test seam.
- The extraction hook is `async (workspace_id, version_id, location_path) -> None`, called after
  commit.

### `knowledge.api` additions

Expose these, with deferred imports of `sync` to avoid a cycle:

- `use_backend_hook(fn | None)`, where `fn(row, built_backend) -> backend` runs in `_backend`.
  `_opened` must still `aclose()` the S3Storage it built.
- `ensure_project_folder(s, project_id, *, net) -> ProjectFolderOut | None`: the row plus the
  `uploads/`, `notes/`, `agent-outputs/` and `.tumnis/` folders.
- `trash_document(s, id)`
- `place_upload(s, project_id, name, data, *, net)`
- `list_document_versions(s, doc_id)` and `get_document_version(s, version_id)`: DTO with `id`,
  `version_no`, `content_hash` (hex), `size` and `body_md`.
- `backup_sources(s)`: objects with `project_id`, `location_id`, `source`, `dest` and `mode`.
  - For a server path, `source` is `f"{root}/{root_path}"`.
  - For S3, `source` is `f"tumnis-{location_id}:{bucket}/{prefix}/{root_path}"`.
  - `dest` is `f"{workspace_id}/{project_id}"`.

### Workflows and storage

- `workflows.folder_sync(workspace_id: str, location_id: str)`: DBOS name
  `knowledge_folder_sync`, on the `sync` queue. After each applied decision n it hits kill point
  `knowledge.folder_sync.applied_{n}`.
- `StorageBackend.ensure_folder(path)`: new on the port.
  - Server path: mkdir -p, marker required.
  - S3: a no-op.
  - Fake: records the folder.
  - Add a contract case to `storage_contract.py` as a new test. Don't edit existing tests.

## Decisions and deviations (report these in the PR)

1. **Note bytes and frontmatter.** T-P1-14-08 and test_pr52 lock `save_note` to write the raw
   body.
   - The sync engine writes notes as `---\ntumnis_id: <id>\n---\n<body>` (the scenario checks
     `startswith`).
   - `save_note` keeps raw bytes and now records `folder_files`. Otherwise the next scan would
     take its file for an outside one.
   - Scott item: a spec change to T-P1-14-08 would let `save_note` write frontmatter too.
2. **Note path.** `save_note` uses the doc's `folder_files` path, else
   `notes/<sanitized title>.md` deduped. Before, it used `<doc id>.md`. The P1-14 tests read
   `queued.path`, so they still pass.
3. **`save_note` precondition.** With `if_match=None` and a record for the path, it uses the
   record's etag.
4. **Draining after a queued save.**
   - A drained write records `synced_version = documents.version` when the doc body still
     equals the snapshot, else 0. With 0, the next scan writes the newer body through.
5. **Drain order.** `folder_sync` drains `pending_writes` right after the health check, before
   listing. The plan puts it at step 7. Doing it first means a queued save lands before the
   comparison and isn't taken for a conflict. `dropped_mount.yaml` relies on this.
6. **Project folder name.**
   - `assign_project_folder` and `ensure_project_folder` name the folder
     `sanitize_filename(project name)`, deduped on the location (T-13 expects `"Acme- Site-"`).
   - `set_project_location`'s fallback keeps `str(project_id)`, because
     `test_pr52_project_made_before_any_location...` locks it.
7. **Which API makes the folders.**
   - `api.assign_project_folder` stays DB-only. T-P1-14-09 asserts that the location root is
     empty after it.
   - The `knowledge.assign_project_folder` subscriber (not renamed) calls
     `ensure_project_folder`.
8. **Linking documents.**
   - `folder_files` holds `path`, `size`, `mtime`, `content_hash` (hex), `etag`, `origin`,
     `document_id` and `synced_version`, plus `delete_confirmed bool` and `last_op text`
     (idempotent apply).
   - Documents created from the folder get `source='folder'`. A doc's origin is external iff
     its source is `folder`.
   - WRITE_NEW candidates are only live `kind='text'` docs of projects on the location that
     have no record. This includes the brief, so the kill test counts 5 notes plus the brief.
   - Keep `documents.storage_location_id` and `documents.path` current.
9. **Local hash.** For a text doc, `Local.content_hash` is the sha256 of `render_note`. For a
   file doc, it is `documents.content_hash` in hex.
10. **Record handling per action.**
    - UNINDEX_ONLY keeps the record, so the file is not re-indexed.
    - TRASH_DOCUMENT, FORGET, MOVE_TO_TUMNIS_TRASH and DELETE_AT_SOURCE drop the record.
11. **Conflict apply.**
    - Row 10: copy the outside bytes to `conflict_path` (create-only) as a new external doc,
      then write Tumnis's bytes at the path with `if_match`.
    - Rows 9 and 11: write Tumnis's bytes to `conflict_path` as a new tumnis-origin doc. The
      original doc gets a new version from the folder.
    - Row 4: the Tumnis doc moves to `conflict_path`, and the outside file becomes a new
      external doc.
    - Each conflict opens one review item (dedupe key).
12. **Migration.** Revision `knowledge_0005` (file `0005_folder_files.py`), with
    `down_revision = "knowledge_0003"` until P1-16's `knowledge_0004` lands, then re-chain it.
    - Add `storage_locations.last_sync_at`.
    - Add `("folder_files", "origin"): "tumnis"` to
      `tumnis/core/tests/integration/row_factory.py` `COLUMN_VALUES`.
13. **Extraction.** Enqueueing P1-16's `extract_document` (source `storage`) waits for P1-16. The
    hook defaults to a no-op until then; wire it after merging P1-16.
14. **Review kinds.** Register the three kinds (owner `knowledge`; actions
    accept/edit/reject/snooze; impact `project`) when `knowledge.api` is imported. The wiring
    imports every api.
15. **Events.** P1-16 owns the `document.added` and `document.changed` payloads (`payloads.py`),
    so this WP does not define them.

## Remaining steps (plan TDD order)

1. `sync_rules.py`: T-01, then T-02 and T-03, then T-04 to T-07. Remove one marker at a time.
   Check 100% line coverage (`--cov=tumnis.modules.knowledge.sync_rules`). Consider adding
   `tumnis.modules.knowledge.sync_rules` to `.importlinter` `rules-are-pure` (a shared file;
   list it in the PR).
2. The migration, the model, the `ensure_folder` port method and `ensure_project_folder`
   (T-13).
3. `sync.py` plus `workflows.folder_sync` and the scenario runner on a server path (T-08),
   then T-09 and T-10.
4. The MinIO scenarios. Then `local_watch`: watchfiles is **not** installed. Add
   `watchfiles==<pin>` to pyproject and uv.lock (shared files), and start it from `worker._serve`
   with a small edit, since P1-16 also edits `_serve`. Also add a 15-minute
   `folder_sync_tick` via `workflows.schedules()`, which the worker picks up with no edit.
5. T-11: `backup_sources`. Then T-12: `tumnis knowledge backup-sources --json` in
   `tumnis/cli.py`, and `scripts/backup/folders.sh`.
   - The script parses JSON with python3 and runs `deploy/rclone/folders-backup.sh SRC
     REMOTE/DEST` for each source.
   - No line may contain `rclone` followed by sync, move or delete (T-P0-28-05 scans scripts/).
6. Run `make check`, `make test` and `make test-int` bare, as vm-agent-rules says.
7. Open the PR. Run the CodeRabbit loop per `pr-review-loop.md`, then merge origin/main once
   P1-16 lands and re-chain the migration.

## Scott items so far

- T-P1-14-08 locks the raw note bytes, which conflicts with the plan's `tumnis_id` frontmatter
  for notes written by `save_note` (decision 1).
- Scheduling the nightly folder copy in compose needs homelab ops: a container with rclone, the
  tumnis CLI, the folder mounts and rclone remote credentials.

## Verify

```bash
cd backend && uv run pytest -q -p no:randomly -m "not integration and not contract" tumnis/modules/knowledge/tests/unit ../scripts/backup/tests
cd backend && uv run pytest -q --collect-only -m integration tumnis/modules/knowledge/tests/integration
make check   # with SEMGREP_SETTINGS_FILE / SEMGREP_LOG_FILE / SEMGREP_VERSION_CACHE_PATH under $TMPDIR
```
