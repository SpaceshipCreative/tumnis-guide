# P3-14 handoff (Existing folders, shares and SFTP) - c1 -> c2

Branch: `wp/P3-14` (push with `/usr/bin/git push origin HEAD:wp/P3-14`).
PR: **#107** (DRAFT, opened early only to get CI; CodeRabbit skipped it as a draft). When PR1
is complete: rewrite the body (see "PR body" below) with `gh pr edit 107 --body-file ...`,
`gh pr ready 107`, then `gh pr comment 107 --body "@coderabbitai review"` ONCE.

## Commits on the branch (c1)

- 6ec071b test(knowledge): P3-14 spec tests (red) - all 19 strict xfails + red seams
- 6fc4b65 feat(knowledge): existing-folder write rules - T-04, T-05 green (markers off)
- (share) feat(knowledge): shares as a server-path kind - T-02 green (marker off)
- cbc8537 feat(knowledge): SFTP storage backend (+ TestFakeStorageForSftp marker off)
- 2c94b7d feat(knowledge): SFTP and share locations, host-key pinning (knowledge_0007)
- 012c6db feat(frontend): SFTP and share locations, host-key confirmation - T-19 green
- this commit: chore handoff + asyncssh moved to runtime deps + T-01/T-03 markers off

## CI evidence so far (run on 2c94b7d)

- contract: all 25 SFTP contract tests XPASS(strict) = T-01 (TestSftpStorage) and T-03
  (`test_hostile_paths_refused`) pass on the container -> markers removed in this commit.
- e2e, performance, version-skew failed: `ModuleNotFoundError: asyncssh` in the image
  (asyncssh was a dev dependency). FIXED in this commit: moved to `[project] dependencies`
  in backend/pyproject.toml (pinned 2.24.0, unchanged), `uv lock` re-run. Re-check CI.
- GitGuardian: "Generic Password" flagged in `test_sftp_auth.py` line ~62 in commits
  1328484 and 2de262b (the previous agent's commits). Probably the literal
  `"Password: "` kbdint prompt string / `validate_password` fixture. NOT a real secret.
  It was committed before c1; per the rules, commits can't be cleaned without a history
  rewrite. Scott item: mark the GitGuardian incident 37763317 as a false positive (test
  prompt text), or tell us how to handle it. Do not rewrite history. gitleaks (security
  job) passed.
- integration: never finished locally (stopped for handoff); nothing verified yet for
  T-09, T-10, T-11, T-12, T-13, T-18. Get it from CI (`gh pr checks 107`, then
  `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs`
  with `allowed_domains: ["*.blob.core.windows.net"]`; grep XPASS/FAILED).

## Spec-test state

Green, markers off: T-01 (sftp + fake), T-02, T-03, T-04, T-05, T-19.
Implemented, markers ON, awaiting a CI integration run: T-09, T-10 (test_sftp_host_key.py),
T-11 (test_sftp_auth.py), T-18 (test_ssrf.py), T-12, T-13 (test_share.py). An XPASS(strict)
in CI is the proof: then delete that marker (Scott-approved step) and commit.
PR2 (impl-2, stubs raise NotImplementedError): T-06, 07, 08 (existing folders), T-14, 15,
16 (move), T-17 (fixture set on sftp/share).

## Local-run constraints (important)

- Docker-backed pytest does NOT work sandboxed (docker socket EPERM) and a single test
  can't be run bare; `dangerouslyDisableSandbox` was DENIED by the classifier - never
  retry it. Only `make test-int` (bare, whole layer, slow) or CI. Loopback from the
  sandbox to a hand-started container does not work either.
- If mypy says `module has no attribute "move"`, delete `backend/.mypy_cache` (stale).
- make check: `bash /tmp/claude-1002/P3-14-c1/check.sh` (sets the semgrep env vars) or
  write your own under `$TMPDIR/P3-14-c2/`.

## What c1 built (design, for the PR body deviations)

- `adapters/sftp.py`: SftpStorage (asyncssh 2.24). Connect to the address
  `resolve_and_check` returned (`sftp_policy`: port 22 + the explicit port in self-hosted
  mode); `known_hosts=([pinned], [], [])`, `config=None`, `preferred_auth=["publickey"]`,
  `password=None`, `password_auth/kbdint_auth/host_based_auth/gss_auth/gss_kex=False`,
  `agent_path=None`, `connect_timeout=10`. HostKeyNotVerifiable -> `HostKeyChanged`
  (StorageError); PermissionDenied -> AdapterRejected("auth_failed"). Paths:
  safe_rel_path + lstat of every segment (symlink or non-folder -> PathRejected). Create:
  upload `.tumnis-tmp-<uuid>` (mode "xb", 0600) then v3 `rename` (OpenSSH does
  link+unlink: no clobber). Replace: fresh hash compare then `posix_rename`; no
  extension -> PreconditionFailed. Etag = sha256; process-wide hash cache keyed (host,
  port, user, path, size, mtime), only for mtimes older than 2 s (racy rule).
  `probe_host_key` = `asyncssh.get_server_host_key` (ed25519 preferred, 10 s timeout).
  Helpers: `fingerprint`, `check_private_key`, `sftp_root`, `openssh_key`.
- `adapters/server_path.py`: `kind` param; share forces network_fs; hash cache
  process-wide (`_HASHES`, bounded, racy rule 2 s). `sync.py`: etag_is_hash for every
  kind but s3.
- `api.py`: `SftpConfig` sealed in config_enc; `_Unopened` backend for SFTP rows not
  pinned / pending / changed (never connects; writes LocationOffline);
  `create_location` share (422 `marker_missing`) and sftp (SSRF 422 `ssrf_blocked`,
  422 `host_unreachable`, key check, saved `pending_host_key` + host_key_pending);
  `check_location` re-checks the SFTP host with the SSRF guard, never opens pending/changed
  (re-probes and shows the key); `_set_status` maps health `host_key_changed` to sticky
  status + audit `storage.host_key_mismatch` + review item `storage_host_key_changed`
  (dedupe per location, registered in api.py, actions accept/snooze, workspace scope);
  `confirm_host_key` (422 `reason_required` when re-pinning without reason, 422
  `fingerprint_mismatch`, audit `storage.host_key_pinned` with reason, drains queue).
  LocationOut: `host_key_sha256`, `pending_host_key_sha256`, endpoint `user@host:port`.
- router: `POST /v1/knowledge/locations/{id}/host-key {sha256, reason?}` (session).
- migration `knowledge_0007` (after knowledge_0006): kind + share; status +
  pending_host_key, host_key_changed (NOT VALID re-add); host_key_pinned/pending columns.
- Frontend: `settings/storage/HostKey.tsx`; StorageSection gains share + SFTP forms, the
  HostKey confirm (reason field on a changed key).

## Remaining steps for PR1

1. Push this commit; watch CI. Fix integration failures of T-09/10/11/12/13/18 (likely
   spots: the ScriptedResolver/LAN host in `_locations.py`; `_set_status` being reached
   with `host_key_changed`; T-13 hash counting with the process-wide cache; T-12 flow).
   Remove each marker once it XPASSes. Earlier-WP integration tests must stay green (the
   share/cache changes touch P1-14/P1-15 paths: watch test_locations, test_folder_sync*).
2. Not yet done: first-party web citations are gathered (below); audit cases for the new
   actions in `backend/tests/audit_cases.py` were NOT added (needs an SFTP server in the
   core audit Ctx; mention as a deviation or add); docs for AGENTS.md: none needed
   (non-HTTP client connects to the resolve_and_check address, as AGENTS.md line 213 says).
3. Write the PR body ($TMPDIR/P3-14-c2/pr-body.md): summary, per-layer results, shared
   files (backend/pyproject.toml + uv.lock: asyncssh 2.24.0 moved from dev to runtime
   deps, same pin, needed by the adapter at runtime), deviations (below), docs cited,
   Scott items; end with a blank line + the Claude Code footer. Then ready + one
   CodeRabbit request, review loop, MERGE-READY message to main.
4. Then PR2 on `wp/P3-14-impl-2` from wp/P3-14: T-06/07/08/17/14/15/16 (existing-folder
   setup + write rules in knowledge.api for every writer, delete-confirmation routes,
   rename_document, move workflow `knowledge_move_project_folder` + migration
   knowledge_0008 folder_moves, review kind folder_move_old_copy, FolderSetup + move UI).

## Deviations (for the PR body)

- Contract layout follows P1-14's per-backend files (`test_storage_sftp.py`,
  `test_storage_share.py`) instead of `test_storage_contract.py::test_contract[x]`.
- Routes under the existing `/v1/knowledge/...` prefix (plan: `/v1/storage/...`).
- Adapter at `adapters/sftp.py` (flat, like s3.py), not `adapters/storage/sftp.py`.
- Host-key probe runs inline at save/test (Scott decision 1), not a queued workflow; no
  UI polling.
- `host_key_changed` is sticky; test re-probes and shows the new key, never re-pins.
- SSRF: SFTP ports = 22 plus, in self-hosted mode, the port the location names.
- The storage_host_key_changed review item is not auto-resolved on re-pin (knowledge
  can't write tasks' table); the user decides it in the queue.
- asyncssh moved from the dev group to runtime dependencies (same pin).
- Red commit registered `knowledge.sftp` (the adapter-registry meta test needs every
  contract class's adapter registered), so TestFakeStorageForSftp was XPASS in that commit.

## Docs relied on (cite in PR body)

- Context7 /ronf/asyncssh (known_hosts: "these seven lists can also be provided
  directly"; validate_host_public_key; get_server_host_key) + installed 2.24.0 source:
  `known_hosts.match_known_hosts` tuple form, `validate_server_host_key` ->
  HostKeyNotVerifiable, SFTPClient.rename (v3 no overwrite) / posix_rename, open mode 'x'
  = FXF_WRITE|FXF_CREAT|FXF_EXCL, SSHClientConnectionOptions auth flags.
  https://asyncssh.readthedocs.io/en/latest/api.html#specifying-known-hosts
- OpenSSH PROTOCOL, posix-rename@openssh.com:
  https://github.com/openssh/openssh-portable/blob/master/PROTOCOL
- OpenSSH sftp-server.c `process_rename` (link+unlink for regular files, no clobber;
  stat+rename fallback when links are unsupported):
  https://github.com/openssh/openssh-portable/blob/master/sftp-server.c

## Scott items

- GitGuardian incident 37763317 ("Generic Password", test_sftp_auth.py, commits 1328484
  and 2de262b): test prompt text, not a secret; needs marking as a false positive on the
  GitGuardian dashboard (agents can't; no history rewrite).
