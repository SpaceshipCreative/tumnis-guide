# P2-18 handoff 3 (Project archive and unarchive)

The context watcher sent HANDOFF NOW. **PR #105** is open: https://github.com/SpaceshipCreative/tumnis-guide/pull/105. Branch `wp/P2-18` on origin has everything below, including this handoff commit.

## State

- **CI.** It has not run green on the latest head yet.
  - Every push until now found the PR `CONFLICTING`, because main kept moving, so the `ci` workflow never ran on a mergeable head. One `ci` run (36769684223) shows FAIL. It was on an older head and was not inspected; check it with `gh run view 36769684223 --log-failed`.
  - `d350f02` merges main (P3-08 thresholds). Its only conflict was `backend/tests/audit_cases.py`, and both cases are kept. After the merge, `make gen` changed nothing and `make check` passed.
  - That merge is committed and pushed together with this handoff.
- **CodeRabbit.** The first review raised 3 actionable threads. All 3 are fixed in `b9e577c`, answered and resolved:
  - `put_blob` digest check, which raises `BlobCorrupt`.
  - `store_archive_step` keeps the facts whatever the state is.
  - Unsent `archive`/`restore` commands are withdrawn after a wait slice (`agents.withdraw_command`).

  `@coderabbitai review` was requested again after `26c77d5`. Its reply has not been read yet.
- **Markers.** Every P2-18 marker is off: T-01/T-02 (daemon) and T-03..T-09 (backend). Each test ran and passed without its marker locally, in the archive subset run of `make test-int`.

## Commits since handoff 2 (on top of `d9ce557`)

- `c6e58fc` test: `project.purged` event fixture.
- `340fc7a` fix(projects): the archive state lives in the new tenant table `project_archives` (`projects_0002`, `depends_on` `core_0009_archived_blobs`). `archived_blobs.id` gets `server_default uuidv7()`.
- `f529cb5` test: fixes to the P2-18 world (run_events kind `log`, `add_chunks`, settle over owner SQL, seed test asks for `app` before `seed`). The markers come off here.
- `83a7190` fix: the workflow's state moves emit no `project.updated`. Such an event outranked the unarchive in the search index (T-P0-20-13).
- `25a4df2` fix: the archive state is read with the project row as a scalar subquery. The query ceiling for `GET /v1/projects` is 6 (T-P0-29). Row factory values for `project_archives.state` and `archived_blobs.codec`.
- `61fd973` merge main (#93 P1-07, #100 P1-15), and `40dc886` then the world: the folder sync extracts nothing, and `sync()` waits for the outbox deliveries.
- `12f7672` merge main (#104 P2-02).
- `b9e577c` CodeRabbit follow-ups, plus tests `core/tests/integration/test_archive_blobs.py` and `agents/tests/integration/test_archive_withdraw.py`. Neither of those two tests has passed locally yet: Docker answered 500 on the VM. Check them in CI.
- `26c77d5` removed the old HANDOFF.md.
- `d350f02` merge main (P3-08).

## Next steps

1. Check that the PR is mergeable (`gh pr view 105 --repo SpaceshipCreative/tumnis-guide --json mergeable`). If main moved again, run `/usr/bin/git fetch origin` then `/usr/bin/git merge origin/main`, keep both sides, run `make gen` and `sh $TMPDIR/P2-18-c3/check.sh` (copy it from `/tmp/claude-1002/P2-18-c2/check.sh` and fix the paths), then push `HEAD:wp/P2-18`.
2. Watch `gh pr checks 105 --repo SpaceshipCreative/tumnis-guide`. Fix real failures. Rerun a cancelled `contract` job once with `gh run rerun <id> --failed`. `preview` stays pending.
3. Read CodeRabbit's reply to the re-review:
   - `gh api repos/SpaceshipCreative/tumnis-guide/pulls/105/comments`
   - review bodies, including the nitpick and outside-diff sections
   - the GraphQL `reviewThreads` query

   Fix or answer each item, then resolve it.
4. When CI is green and no threads are open, SendMessage "#105 MERGE-READY at <sha>" to main.
5. Delete this HANDOFF.md in a chore commit.

## Local test notes

- Docker on the VM often answers 500 when it starts a container. Rely on CI for the Docker layers.
- To run only a subset of `make test-int`, temporarily prepend test paths to `addopts` in `backend/pyproject.toml`, and add `--runxfail` to see xfail reasons. **Restore the file before any commit.**
- `test_rclone_copy_keeps_files_removed_at_source` fails on the VM only; it is known.

## Deviations and Scott items

The PR body has the full lists; keep it current with `gh pr edit 105 --repo ... --body-file`. The latest body is at `/tmp/claude-1002/P2-18-c2/pr-body.md`.

**Deviations**
- No archive UI.
- The `archive` queue has a global `concurrency=1` and is not partitioned (T-08 enqueues without a partition key).
- The state lives in `project_archives`.
- `finish` emits no event.
- A pack over 50 MiB is archived index-only.
- Excerpts = `context_items`.
- A purge soft-deletes the project and drops what the archive kept.
- A failed restore leaves the project `unarchiving`.
- An unsent runner command is withdrawn after one hour.
- Busy guard.

**Fixes to this WP's own spec-test support code** (no assertion changed): `_archive.py` and the fixture order in the seed test.

**Shared files**
- `pyproject.toml`/`uv.lock` (zstandard 0.25.0) for backend and daemon.
- `.importlinter`.
- `tests/audit_cases.py`.
- `worker.py`.
- `core/tests/integration/row_factory.py`.

**Scott items**
- Archive UI is left for a later WP. Is that OK?
- How deep should a purge go? Should it also hard-delete tasks and documents?

## Docs cited

- Context7 `/indygreg/python-zstandard`.
- Context7 `/dbos-inc/dbos-transact-py`.
- Context7 `/websites/sqlalchemy_en_20` (`on_conflict_do_update`, RETURNING).
- The Python 3.13 `tarfile` docs.
