# HANDOFF: P1-16 Upload safety and extraction (sixth handoff, end of c5)

Impl PR **#69** (https://github.com/SpaceshipCreative/tumnis-guide/pull/69), branch `wp/P1-16`, head **778b54c**.
Spec PR **#83** (https://github.com/SpaceshipCreative/tumnis-guide/pull/83), branch `wp/P1-16-spec-t03-bound`,
base `wp/P1-16`, head **747f91e**. It holds decision 21's T-03 bound (`+ 2 * MIB`), the matching docstring
sentence and (new in c5) the removal of T-03's strict xfail. It has the `spec-change` label and was OPEN at handoff.

**Why c5 stopped:** CI `security` is red repo-wide. Trivy flags CVE-2026-75804 and CVE-2026-84782 (OpenSSL
`3.5.7-1~deb13u2`, fixed in `deb13u3`) in the pinned `python:3.13-slim` base. Another agent fixes it in **#90**
(`fix/openssl-cves`). The coordinator said: do NOT touch `deploy/Dockerfile` in P1-16, and hold until #90 merges.

Setup: throwaway branch at main in a worktree, then `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`,
`/usr/bin/git merge origin/wp/P1-16`. Push only with `/usr/bin/git push origin HEAD:wp/P1-16`.
Read `~/tumnis-coordinator/vm-agent-rules.md` and `scott-decisions.md` (items 10, 11, 12, 14 and 21 are P1-16's).
Sandbox gotchas:
- Run one plain `/usr/bin/git` command per call, with no `$(...)`, `cd X &&` or pipes next to git or gh. For anything
  more complex, write a script with the Write tool and run `bash <script>`.
- `make check` needs `SEMGREP_*` env vars; `/tmp/claude-1002/P1-16-c5/check.sh` sets them and logs to `check.log`.
- CI logs: `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs` with
  allowed_domains `*.blob.core.windows.net`. Get the job id with a separate `gh api .../commits/<sha>/check-runs` call.
- `/tmp/claude-1002/P1-16-c5/wait_ci.sh <sha>` polls check runs until everything except `preview` completes.
  Read the conclusion column: `cancelled` counts as done.
- `/tmp/claude-1002/P1-16-c5/threads.sh <pr>` lists unresolved review threads.

## Commits in c5

On wp/P1-16 (#69):
- 88c1854: merged main 2b04f58 (#77 P1-12). Clean; `make gen` unchanged; one knowledge head (`knowledge_0006`).
- 326dc34: `knowledge_0006` writes `chunk_tsv`'s body as literal SQL. It was the last f-string, flagged by
  CodeRabbit beside the status checks, and the emitted SQL is byte-identical. Also drops the dead `STATUSES` constant.
- 778b54c: merged main 32863b4 (#79 Vitest fixes, #86 spec-guard merge-base). `make check` is fully green locally,
  Vitest included (144/144).

On wp/P1-16-spec-t03-bound (#83), all built with plumbing (merge-tree / commit-tree), HEAD never moved:
- 131c4c9: merged wp/P1-16 (0380fec) into #83.
- 6120d13: removed T-03's `@pytest.mark.xfail(strict=True, reason="spec:P1-16")`. CI at 131c4c9 showed
  `XPASS(strict)` for T-03 under the +2 MiB bound, so #83 could never be green with the marker on. This is the
  approved unmark (the test passed in CI). The coordinator was told, and a comment on #83 explains it.
- 747f91e: merged wp/P1-16 (88c1854) into #83. `git diff origin/wp/P1-16 747f91e` is T-03 only.

## CI state

- #69 at 778b54c: everything is green except `security` (Trivy only; semgrep 0 findings). `preview` stays queued.
- #83 at 747f91e: everything is green except `security` (same Trivy failure). Integration passes T-03 unmarked.
- Review threads: 0 unresolved on #69 and #83. `@coderabbitai review` was requested on #69 after the c5 body update.
- #69 body: updated in c5 (local copy `/tmp/claude-1002/P1-16-c5/body69.md`).

## NEXT STEPS (the coordinator's order; start once #90 has merged into main)

1. `/usr/bin/git fetch origin`, then `/usr/bin/git merge --no-edit origin/main`, `make gen`, and
   `bash /tmp/claude-1002/P1-16-c5/check.sh`. Push `HEAD:wp/P1-16`. Run `wait_ci.sh <sha>`; security should now pass.
2. Bring #83 up to date with wp/P1-16 using the plumbing pattern in `/tmp/claude-1002/P1-16-c5/refresh83b.sh`:
   merge-tree the current #83 head with `origin/wp/P1-16`, commit-tree with both parents, check that
   `git diff origin/wp/P1-16 $C` is T-03 only (marker line, bound, docstring), then push
   `$C:refs/heads/wp/P1-16-spec-t03-bound` (a fast-forward; never force). Wait for CI green, then SendMessage main:
   `#83 MERGE-READY at <sha>`.
3. After the coordinator merges #83 into wp/P1-16: fetch, then `/usr/bin/git merge origin/wp/P1-16`. Confirm the
   #83 head is an ancestor and T-03 has no marker. Delete this HANDOFF.md in a `chore:` commit, run `make check`, and
   push. Wait for CI green, check `threads.sh 69`, then SendMessage main: `#69 MERGE-READY at <sha>`.

## Scott items (keep)

- **Decision 21 / #83:** CodeRabbit's point stands. With a 51 MiB file, early refusal and reading the whole body look
  the same except for the closing tail, and neither bound closes that gap. A possible follow-up spec change:
  `MAX + MIB + head bytes`, or a bigger file.
- **P1-15 folder files skip the scan.** `place_upload` and `create_file_document` insert documents that get
  `status='ready'` from knowledge_0006's default, so `GET /v1/files/{id}` would serve them unscanned. P1-15's
  extraction-hook follow-up (`sync.request_extraction` -> `knowledge_extract_document`) should create them as `pending_scan`.
- **Stale docstring.** P1-15's `0005_folder_files.py` still says "re-chained after P1-16's knowledge_0004". Left untouched.
- **Still open:** Docling in CI for impl-2 (CPU torch wheels, prefetched models; decision 8 covers the budget).
- **Housekeeping:** stale worktree entry `.git/worktrees/p116-spec-eicar`.

## Deviations (beyond those already in the #69 body)

- c5: T-03's marker is removed inside #83 instead of after #83 merges, because the strict xfail made #83 red once
  T-03 passed.
- c4: #83 also changes T-03's docstring sentence to match the new bound.
- c4: the folder watcher doesn't run in `worker-extract`; `coolify-poll` is on the `sync` queue; the name rules are
  shared in `rules.py`; `document_versions.body_md` stays NOT NULL (`''` until extracted); both status checks are
  NOT VALID; the spool directory is made lazily.

## Verify commands

- `cd backend && uv run alembic heads` shows exactly one `knowledge_0006 (knowledge) (head)`.
- `cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main` reports `passed ... knowledge_0006`.
- `make test` and `make test-int` must run bare. On the VM, integration often hits Docker 500s under load, so rely on CI.

## PR 2 (`wp/P1-16-impl-2`), after #69

- T-06: real Docling and `*.expected.yaml`.
- T-13: the vision adapter. Keep the parked test in `handoff/P1-16/.../test_vision_contract.py` until then.
- A1.5: `knowledge_app` wiring, `test_eicar_upload_is_quarantined` and `test_pdf_is_scanned_extracted_and_filed`.
