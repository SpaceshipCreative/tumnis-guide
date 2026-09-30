# HANDOFF: P1-16 Upload safety and extraction (third handoff)

Stopped on the coordinator's "HANDOFF NOW" (context watcher). The impl PR is NOT open yet.
Spec-change PR #67 is open (see "Scott items").

Start: worktree on a throwaway branch at main; `/usr/bin/git fetch origin`,
`/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P1-16`; push only with
`/usr/bin/git push origin HEAD:wp/P1-16`. Read `~/tumnis-coordinator/vm-agent-rules.md`,
`scott-decisions.md` (10, 11, 12 concern P1-16) and `~/tumnis-coordinator/prompts/P1-16.md`.
Sandbox gotchas: heredocs, `cd X && git ...`, `git -C`, variables in `sed` args and
`rtk`-looking pipelines are refused; use Write/Edit for files, one plain command at a time,
`/usr/bin/git` from the worktree root. `make test-int` prints nothing until it finishes
(about 10 min); run it with `run_in_background`, then `rtk recall <id>` the full output.

## Commits on `wp/P1-16`

- `402220b` spec tests (red); `9f34944` AGENTS.md decisions 10/11; `97d2165` rules;
  `f35c5ce` ClamAV client + FakeClamAV; `0a64c51` second handoff (migration + models WIP).
- `a244ca3` merge of wp/P1-16 onto main e5e5d36+ (api.py conflict: kept P0-24's
  `project_of`/`register_project_lookup("knowledge")`/`update_text_document` AND P1-16's block).
- This handoff's commits (see `git log`): the implementation, marker removals, docs, this file.

## State (all green locally unless listed)

Implemented: `knowledge/store.py`, `payloads.py` (+ `events.py` re-export, contract fixtures
`backend/tests/contract/fixtures/events/document.{added,changed}/v1.json`), `pipeline.py`
(all step bodies), `workflows.py` (`knowledge_extract_document`, kill point
`extract.vlm_step` in the workflow body), `uploads.py` (streamed multipart to the spool),
`api.py` (`begin_upload`, `ingest_folder_file`, `enqueue_extract`, `get_document`,
`check_upload_target`, `file_location`, `storage_path`, `download_info`, `stream_file`),
`router.py` (`POST /v1/knowledge/documents`, `GET /v1/knowledge/documents/{id}`,
`GET /v1/files/{id}`), `worker.py`/`cli.py` (`--queues`, `extract` queue, `listen_queues`,
`MAIN_QUEUES`, executor `worker-<queues>`, no relay/schedules for a queue worker),
`deploy/compose.yaml` (`clamd`, `worker-extract`, volumes `spool`, `scratch`, `clamd_db`),
`deploy/compose.preview.yaml` (clamd behind profile `clamd`), `deploy/clamd/clamd.conf`,
`deploy/Dockerfile` (`libmagic1`, spool/scratch dirs), `make gen` output, frontend
`live-map.ts` NOT_LIVE (`knowledgeGetDocument`, `knowledgeGetFile`), plan Part A row,
migration fixes (GRANT EXECUTE on `chunk_tsv` to tumnis_app; downgrade fills NULL body_md),
FakeDocling strips NUL, `_phase1.py` `chunks_of` join via `documents.current_version_id`,
`scripts/fixtures/make_extraction_fixtures.py` (moved from handoff/).

Markers removed (tests pass in `make test-int`): T-01, T-02 (3 cases), T-07, T-08, T-10,
T-11, T-12 and T-14. Still strict xfail: T-03, T-06 (impl-2), A1.5 (impl-2), T-13 (parked in
`handoff/P1-16/.../test_vision_contract.py`, KEEP it there; coordinator instruction).

Last `make test-int` (before the T-03 re-mark): 3 failed, 850 passed. Failures:
- T-03 `test_51_mb_refused_50_mb_accepted`: answered `body_too_large` (the P0-10 body-limit
  middleware, `max_body_bytes = MAX_UPLOAD_BYTES + 1 MiB`) instead of the handler's
  `too_large`. Fix idea: raise the route's `max_body_bytes` (e.g. `+ 2 MiB`) so the
  handler's own file counter fires first. CAUTION: the test also asserts
  `sent[0] <= MAX_UPLOAD_BYTES + MIB`; the client yields the multipart head (~150 bytes) and
  then 1 MiB chunks, so the file passes the limit only on the 51st chunk and `sent` is then
  head + 51 MiB, a little over. If that assertion cannot hold, stop and ask Scott (spec test).
  Marker re-added.
- `test_rclone_copy_keeps_files_removed_at_source`: known local-only.
- `test_idempotency.py::test_same_key_same_body_replays_stored_response` and two MinIO ERRORs
  (`test_s3_conditional_writes`, `test_location_credentials_encrypted`): look environmental
  (port collisions/load; the first run had different ones). Check in CI.
- `TestClamAV.test_eicar_inside_a_larger_file_is_found` (real clamd) fails and STAYS failing
  in this PR: see Scott items. Do not touch the test here.
`make check`: clean except `ProjectList.test.tsx` timing out under load (passes alone).

## Remaining steps, in order

1. Push, then let CI run (Docker layers). Fix real failures.
2. T-03 (see above).
3. Open the PR `[P1-16] impl: Upload safety and extraction` with the body below, comment
   `@coderabbitai review`, work the review loop (`~/tumnis-coordinator/pr-review-loop.md`).
4. Delete this HANDOFF.md in a `chore:` commit when done (or update it on the next handoff).
5. PR 2 on `wp/P1-16-impl-2`: T-06 (real Docling + `*.expected.yaml`), T-13 (vision adapter,
   restore the parked test), A1.5 `knowledge_app` wiring (MinIO default location, folders for
   the seed projects, real ClamAV; `upload()` helper must build `eicar.txt` via
   `_samples.eicar()`), `test_eicar_upload_is_quarantined` and
   `test_pdf_is_scanned_extracted_and_filed`. Do NOT add Docling/torch to the default CI
   install in PR 1.

## Decisions and deviations (PR body)

- Workflow signature `(workspace_id, version_id, source)` (plan: `(version_id, source)`):
  steps need a workspace for RLS. Id `extract:<version_id>`.
- Kill point `extract.vlm_step` in the workflow body before `vlm_step`.
- `GET /v1/knowledge/documents/{id}` added (minimal status read; P1-17 owns CRUD).
- Router unprefixed, explicit `/knowledge` paths (already so on main after P0-24).
- Upload route `idempotent=False` (the idempotency layer buffers bodies; a retry is a new
  document), no `project_param` (project is a form field): the handler calls `authorize`
  itself and answers 404 for a project outside a key's limit.
- `knowledge_0004`: `chunk_tsv` IMMUTABLE SQL wrapper (generated column); GRANT EXECUTE to
  tumnis_app (functions are private by default); `document_versions.body_md` nullable;
  `chunks.document_version_id`.
- Upload names: `upload_file_name`/`numbered_name` in rules (P1-15 not merged).
- Scratch copy at `<scratch>/<version_id>/<name>` (the extractor reads the extension).
- `pipeline` is configured by the worker through `importlib` (api -> pipeline would be an
  import cycle; pipeline imports api for `open_backend`).
- A1.5 EICAR acceptance moved to impl-2 (needs the same `knowledge_app` wiring as the PDF one).
- `main`'s `project_of` (P0-24) serves `lookup:knowledge`; no separate `project_of_document`.
- FakeDocling strips NUL from decoded text (Postgres text has no NUL).
- Shared-file edits: `backend/tests/fixtures/__init__.py`, `tumnis/testing/run_worker.py`,
  `tumnis/worker.py`, `tumnis/cli.py`, `tumnis/settings.py`, `AGENTS.md`,
  `backend/pyproject.toml`/`uv.lock` (python-magic, python-multipart),
  `tests/meta/test_compose.py` (T-14 marker), `tests/acceptance/_phase1.py` (helper),
  `deploy/*`, `frontend/src/lib/live-map.ts`, generated files,
  `docs/IMPLEMENTATION-PLAN-DETAILED.md` Part A, `scripts/README.md`.

## Scott items

- Decided: 10 (api reads storage to serve files), 11 (ClamAV connects to clamd directly),
  12 ("Wrap EICAR in a zip").
- Decision 12 is spec-change PR #67
  (https://github.com/SpaceshipCreative/tumnis-guide/pull/67), branch
  `wp/P1-16-spec-eicar-zip` at `0c5f2cf`, based on and targeting `wp/P1-16` (the test is not
  on main). Scott adds the `spec-change` label. Until it merges, the impl PR is red on
  `TestClamAV.test_eicar_inside_a_larger_file_is_found`: leave the test as on origin.
  Evidence: image `clamav/clamav-debian@sha256:9bb8712a…`, `ClamAV 1.5.4/28137/Mon Sep 28
  06:24:12 2026`; the old body (70,000 x + EICAR) scans `infected=False`; `sigtool
  --find-sigs=Eicar` shows only whole-file hashes (`.hdb`/`.hsb`, 68 bytes) and the bytecode
  `Eicar-Signature` anchored at offset 0; `clamscan` on the new zip body finds
  `Eicar-Test-Signature`.
- Open: Docling in CI for impl-2 (torch CPU wheels, prefetched models; decision 8 on the budget).
- Possible: T-03's `sent <= MAX + 1 MiB` bound (see above).
- Housekeeping: a stale git worktree entry `.git/worktrees/p116-spec-eicar` (directory gone,
  "prunable"; `git worktree prune` hit "Device or resource busy").

## Verify commands

- `make check` (root; set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`,
  `SEMGREP_VERSION_CACHE_PATH` to files under /tmp/claude-1002 for the semgrep meta test).
- Unit: `cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/modules/knowledge`.
- Docker layers, bare from the worktree root: `make test`, `make test-int`.
