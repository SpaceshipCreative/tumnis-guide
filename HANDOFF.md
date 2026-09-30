# P3-14 handoff (Existing folders, shares and SFTP)

Branch: `wp/P3-14` (pushed with `/usr/bin/git push origin HEAD:wp/P3-14`). No PR yet. No CI yet.
Base: main at c861cdd (#100 merged). Task prompt: `/tmp/claude-1002/coord7/p3-14.txt`.

## State

One commit on top of main: `chore: P3-14 handoff (WIP red spec tests)`. It holds all 19 spec
tests written as strict expected failures, the test helpers they need, and the first
red-phase seam (write-policy stubs in `rules.py`). **`make check` is NOT green yet**: mypy
fails until the remaining red-phase seams below exist (repo convention, see commit 24100b6:
NotImplementedError stubs keep mypy and tsc green in the red commit). The red commit
`test(knowledge): P3-14 spec tests (red)` has not been made as such; finish the seams, run
`make check`, then commit (amend is not allowed on a pushed commit; make a new commit).

Verified so far: the unit and non-Docker contract spec tests run and xfail
(`test_write_policy.py`, `test_storage_share.py`, `TestFakeStorageForSftp`); every
integration test collects; the Vitest spec `HostKey.test.tsx` is an expected fail; tsc and
ESLint are clean.

## Spec tests (all `xfail(strict=True, reason="spec:P3-14")`)

| ID | Where |
| --- | --- |
| 01 | `knowledge/tests/contract/test_storage_sftp.py::TestSftpStorage` (+ `test_storage_fake.py::TestFakeStorageForSftp`) |
| 02 | `knowledge/tests/contract/test_storage_share.py::TestShareStorage` |
| 03 | `knowledge/tests/contract/test_storage_sftp.py::test_hostile_paths_refused` |
| 04, 05 | `knowledge/tests/unit/test_write_policy.py` |
| 06, 07, 08 | `knowledge/tests/integration/test_existing_folder.py` |
| 09, 10 | `knowledge/tests/integration/test_sftp_host_key.py` |
| 11 | `knowledge/tests/integration/test_sftp_auth.py` (in-process asyncssh server) |
| 12, 13 | `knowledge/tests/integration/test_share.py` |
| 14, 15, 16 | `knowledge/tests/integration/test_move.py` |
| 17 | `knowledge/tests/integration/test_folder_sync_fixtures.py::test_folder_sync_set_on_sftp_and_share` |
| 18 | `knowledge/tests/integration/test_ssrf.py` |
| 19 | `frontend/src/components/settings/storage/HostKey.test.tsx` |

Helpers (not locked test files): `backend/tests/_services.py` (SftpEndpoint gains `container`,
`exec`, `server_path`, `plant_symlink`, async-context `rotate_host_key` that restores the
original key; sshd reloads on `kill -HUP 1` - verify against the image), 
`knowledge/tests/_sftp.py`, `knowledge/tests/integration/_locations.py`,
`knowledge/tests/integration/_folder_runner.py` (backends `share` and `sftp`, `mode`
existing, `move_log`, async unmount/remount).

## Remaining red-phase seams (to make mypy green), then the red commit

The interfaces the tests already use (implement as stubs raising NotImplementedError first):

- `knowledge/adapters/sftp.py`: `HostKeyChanged(StorageError)`; `ProbedKey(openssh, sha256)`;
  `async probe_host_key(host, port, *, net_policy, resolver) -> ProbedKey`;
  `SftpStorage(AdapterBase)` with `name = "knowledge.sftp"`,
  `__init__(*, host, port, username, private_key_pem: bytes, pinned_host_key: str, root: str,
  net_policy=None, resolver=system_resolver, clock=None, policy=None)`, the StorageBackend
  methods and `aclose()`. Register `knowledge.sftp` in `adapters/__init__.py` (fake:
  FakeStorage).
- `knowledge/adapters/server_path.py`: `ServerPathStorage(root, *, kind="server_path" |
  "share", ...)`, with `.kind`; share forces `network_fs=True`.
- `knowledge/api.py`: `LocationKind` += `"share"`, `"sftp"`; `SftpConfigIn(host, port=22,
  username, private_key)`; `LocationIn.sftp`; `LocationOut.status` +=
  `"pending_host_key"`, `"host_key_changed"`; `LocationOut.host_key_sha256`,
  `pending_host_key_sha256` (default None); `confirm_host_key(s, location_id, sha256, *,
  reason=None, net, resolver=system_resolver)`; `use_existing_folder(s, project_id, *,
  location_id, path, net, resolver=...)`; `rename_document(s, document_id, *, title) ->` a DTO
  with `.title`; `delete_document(s, document_id, *, actor: ActorKind)`;
  `create_location`/`check_location` already take `resolver`. Then `make gen` (DTOs change
  OpenAPI and the TS client).
- `knowledge/move.py`: `use_fault(fn: Callable[[str, bytes], bytes] | None) -> previous`.
- `knowledge/workflows.py`: `@DBOS.workflow(name="knowledge_move_project_folder") async def
  move_project_folder(workspace_id, project_id, to_location, to_path) -> dict` (returns
  `{"status": "switched"|"failed", "reason": ...}`); kill point
  `knowledge.move_project_folder.batch_<n>`; runs on the `sync` queue.

## Design decisions made (put in the PR body as deviations)

- Contract layout follows P1-14's per-backend files (`test_storage_sftp.py`,
  `test_storage_share.py`) instead of the plan's `test_storage_contract.py::test_contract[x]`.
- Routes follow the existing prefix `/v1/knowledge/...` (plan says `/v1/storage/...`):
  `POST /v1/knowledge/locations/{id}/host-key {sha256, reason?}`;
  `POST /v1/knowledge/projects/{id}/folder {mode:"existing", location_id, path}` (PUT on the
  same path is P1-14's location move); `DELETE /v1/knowledge/documents/{id}` (200
  `{outcome}`; an API key/task token on an outside file -> 403 `external_delete_forbidden`,
  audit `knowledge.external_delete_refused` recorded in its own transaction so the 403 does
  not roll it back); `POST .../delete-confirmation` (session; 200 `{confirm_token}`, sealed
  with the workspace data key, no new column); `POST .../delete-at-source {confirm_token,
  reason}` (session; 202; bad token 422 `confirmation_invalid`; audit
  `knowledge.deleted_at_source` with the reason; sets `folder_files.delete_confirmed`, the
  next sync deletes).
- Adapter path `adapters/sftp.py` (flat, like `s3.py`), not `adapters/storage/sftp.py`.
- Host-key probe runs inline at location save/test (Scott decision 1 covers location save
  and connection tests from the api process), not as a queued workflow; no UI polling.
- Status `host_key_changed` is sticky: such a location is never opened; `/test` re-probes and
  shows the new key as pending; re-pin needs a reason (422 `reason_required`), audited
  `storage.host_key_pinned`. Mismatch: audit `storage.host_key_mismatch` + review item
  `storage_host_key_changed` (dedupe per location).
- SFTP etag = sha256 hex (hash cached per (path, size, mtime) in the adapter); create =
  write `.tumnis-tmp-<uuid>` then plain v3 `rename` (OpenSSH refuses an existing target);
  replace = fresh hash compare then `posix_rename`; every path `safe_rel_path` then `lstat`
  per segment, symlinks refused. asyncssh options: `known_hosts=([pinned], [], [])`,
  `config=None`, `preferred_auth=["publickey"]`, `password=None`, `password_auth=False`,
  `kbdint_auth=False`, `agent_path=None`, `gss_host=None`, `host_based_auth=False`,
  `connect_timeout=10`; probe with `server_host_key_algs` preferring ed25519, `config=None`.
  SSRF: `resolve_and_check(host, port, sftp_policy(net))` (adds 22 and, self-hosted, the
  explicit port), connect to the returned address.
- Share: `kind="share"` = ServerPathStorage(network_fs=True); setup refuses a share without
  the marker (422 `marker_missing`); unwatched already (`watched_roots` filters
  `kind == "server_path"`); `sync.plan` `etag_is_hash` for every kind but s3; the server-path
  hash cache becomes process-wide and "racy-git" safe (cache only when mtime is older than
  ~2 s at hashing time) so a scan with nothing changed hashes nothing (T-13).
- Migration `knowledge_0007` (after main's head knowledge_0006): kind check + `share`,
  status check + `pending_host_key`, `host_key_changed` (drop + re-add NOT VALID, like
  agents_0002); `host_key_pending`, `host_key_pinned` text columns. impl-2 adds
  `knowledge_0008` `folder_moves`. PR #102 (P1-16 impl-2) adds no knowledge migration.
- Existing mode: every Tumnis write under `<folder>/Tumnis/` (notes, uploads,
  agent-outputs, conflict copies of outside files, trash `Tumnis/.trash/`).

## Plan split

PR1 `wp/P3-14` "[P3-14] impl: Existing folders, shares and SFTP": red commit with all 19,
then green T-04, 05, 01, 03, 11, 09, 10, 18, 19, 02, 12, 13. PR2 `wp/P3-14-impl-2` from it:
T-06, 07, 08, 17, 14, 15, 16 + FolderSetup/move dialog UI.

## Still to do for PR1

Third-party research: Context7 done for asyncssh 2.24.0 (known_hosts list form,
server_host_key_algs defaulting to the trusted keys' algs, preferred_auth, config=None,
posix_rename/rename semantics, verified in the installed source). Still needed: first-party
web check (asyncssh docs host-key validation; OpenSSH PROTOCOL `posix-rename@openssh.com`
and sftp-server rename-no-clobber) and citations in the PR body. Audit cases for the new
SEC-3 actions in `backend/tests/audit_cases.py`. `register_review_kind` for
`storage_host_key_changed`. HostKey.tsx + wiring into StorageSection (SFTP/share forms).

## Verify

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/knowledge/tests/unit/test_write_policy.py`
- `make check` (set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` under `$TMPDIR/P3-14-c1/`)
- `make test`, `make test-int` (bare), `npx vitest run src/components/settings/storage` in frontend.

Scott items: none yet.
