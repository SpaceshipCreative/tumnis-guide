# HANDOFF: P1-16 Upload safety and extraction (fourth handoff)

This handoff was written at the context limit, in the middle of merging main. The impl PR is open as **#69**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/69). The spec PR is **#67**, with the `spec-change`
label (decision 14). #67 is green and CodeRabbit is clean on it; the coordinator merges it into wp/P1-16.

Setup: work in a worktree on a throwaway branch at main. Run `/usr/bin/git fetch origin`, then
`/usr/bin/git merge origin/main`, then `/usr/bin/git merge origin/wp/P1-16`. Push only with
`/usr/bin/git push origin HEAD:wp/P1-16`. Read `~/tumnis-coordinator/vm-agent-rules.md`, including the
NEW "Third-party docs and research" rule (Context7 first, then first-party web; cite sources in the PR body),
and `scott-decisions.md` (items 10, 11, 12 and 14 concern P1-16).

Sandbox gotchas:
- Heredocs are fine inside python3 scripts.
- `cd X && git`, `$(...)` or variables inside gh/git/make commands, and complex jq in loops are refused. Run one plain command at a time and use `/usr/bin/git`.
- `make check` needs `SEMGREP_*` env vars under /tmp/claude-1002/sg. Vitest times out under VM load; `cd frontend && npx vitest run --maxWorkers=2` passes.
- CI job logs: `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs`, with allowed_domains `*.blob.core.windows.net`.

## NEXT STEPS (in order)

1. **Merge origin/main (45a4f00 or later) into wp/P1-16.** My attempt conflicted in `knowledge/api.py`, `events.py`, `models.py`, `workflows.py` and `worker.py`, so I aborted it. Resolve by keeping both sides.
   - **Migration (coordinator order).** P1-15's `knowledge_0005` (0005_folder_files.py) has down_revision `knowledge_0003`, as does our `knowledge_0004`.
     - Re-chain OUR migration to follow `knowledge_0005`, and renumber it to `knowledge_0006`: rename the file, then update the revision id, down_revision and every reference, including plan Part A's "Revision `knowledge_0004` (after `knowledge_0003`" row.
     - Do NOT touch 0005.
     - Check that `alembic heads` shows one knowledge head, and that migrate up and down both work (the integration layer covers the migrations).
   - **P1-15 overlap.** P1-15 may define `upload_file_name` / `numbered_name` in rules.py too; ours are in `knowledge/rules.py`. Keep one copy. P1-15 is meant to reuse ours, and its follow-up wires its extraction hook to our extract workflow.
   - **worker.py.** Main added a coolify schedule (`coolify-poll`, P2-14). The unit job on #69 FAILED because `test_every_schedule_runs_on_a_main_worker_queue` (ours, `core/tests/unit/test_worker_listen.py`) found `coolify-poll` with no `queue_name`. It would run on DBOS's internal queue, which `worker-extract` services too. Fix it by giving that schedule a named main-worker queue: `maintenance`, or whatever fits P2-14's code. Check the coolify module's `schedules()` and `tests/integration/test_poll.py`, which only asserts the name and cron. Mention the change in the PR body as a shared-file edit.
   - After merging, run `make gen` and `make check`, plus unit, `make test` and `make test-int` (background). Then push.
2. Comment `@coderabbitai review` on #69 and work any new threads. So far every thread is answered and resolved; see the list below.
3. Update the PR body (a copy is at /tmp/claude-1002/p116-body.md, which may be gone; `gh pr view 69 --json body` has it) with the migration renumber and the coolify queue change.
4. When #69 is clean, delete this HANDOFF.md in a `chore:` commit, then write the final report.

## State of #69 (head b8419e7 before the merge)

CI at b8419e7:
- Green: lint, security, integration, e2e, traceability, red-proof, spec-guard, version-skew, daemon, skills, performance.
- `contract` is red only on `TestClamAV.test_eicar_inside_a_larger_file_is_found` (expected until #67 merges; do not touch that test).
- `unit` is red on the coolify-poll guard (see step 1).
- `preview` stays pending (no runner).

Commits this session:
- 6f3e4bd: merged main 6b85801. worker.py now uses `main_queues()` and includes P1-04's `runs` and `agents-sweep` queues. python-multipart pinned to 0.0.32, because pip-audit flagged 0.0.26.
- c6556a3: body-limit backstop raised to MAX+2 MiB, so the handler's `too_large` fires first.
- 00d9fcb: `compose.test-overrides.yaml` gives `worker-extract` the checkout's image (e2e was denied pulling ghcr); `hmac.compare_digest` (semgrep).
- 411e980: merged main (#64 test-reset race fix).
- a3ae8cb: a second file part is refused; `set_content` / `set_sniffed` raise NotFound.
- 405ddc8: file serving is current version only, with 409 while a newer version is unreleased; new `test_file_serving_versions.py`.
- 4486da8: deleted the previous HANDOFF.
- 70098b3: MultipartParseError answers 422 `invalid_upload`.
- f0c0929: merged main 27bef71; added the `test_worker_listen.py` guard.
- 71917df: a truncated body (no on_end) answers 422; upload multipart body declared via `openapi_extra`.
- 4c653fe: day-close tick moved to the `maintenance` queue; schedule-queue guard test.
- b8419e7: zod `~resolvers` maps `format: binary` to `z.instanceof(Blob)` (`frontend/openapi-ts.config.ts`).

CodeRabbit threads on #69, all replied to and resolved:
- Fixed:
  - `set_content` missing version (a3ae8cb)
  - duplicate file part (a3ae8cb)
  - truncated multipart (71917df)
  - day-close on the internal queue (4c653fe)
  - OpenAPI requestBody (71917df)
  - zod binary (b8419e7)
- Partly fixed: version snapshots (405ddc8). Old versions answer 409 rather than being served from snapshots; per-version snapshots are left for P1-17.
- Declined: per-replica executor ID. There is one `worker-extract` and a stable ID is what DBOS recovery needs.

## Scott items (keep)

- **Decision 12 / #67.** The EICAR zip spec change. #67 CI is green; its contract job passes the zip case against real clamd 1.5.4. Until it merges into wp/P1-16, #69's contract job is red on that one case.
- **T-03's `sent[0] <= MAX_UPLOAD_BYTES + MIB` cannot hold (spec test, marker kept).** The client sends a multipart head of about 180 bytes, then 1 MiB chunks. A file larger than MAX can only be detected in the 51st chunk, so `sent` is at least head + 51 MiB, which is MAX + MIB + len(head). The handler now answers `too_large`. Scott should decide on a new bound (for example `+ 2 * MIB`); the assertion has not been edited.
- **Open.** Docling in CI for impl-2 needs CPU torch wheels and prefetched models (decision 8 covers the budget).
- **Housekeeping.** There is a stale worktree entry `.git/worktrees/p116-spec-eicar`.

## Deviations (in the PR body already)

- Workflow signature is `(workspace_id, version_id, source)`.
- The kill point sits in the workflow body.
- `GET /v1/knowledge/documents/{id}` was added.
- The upload route is `idempotent=False` and has no `project_param`.
- `knowledge_0004` details (chunk_tsv IMMUTABLE wrapper + GRANT, nullable body_md, chunks.document_version_id).
- Upload names live in rules.py.
- Scratch path is `<scratch>/<version_id>/<name>`.
- Pipeline is configured via importlib.
- Main's `project_of` is used.
- FakeDocling strips NUL.
- A1.5 EICAR moved to impl-2.
- `MAIN_QUEUES` became `main_queues()`.
- Body backstop is MAX+2 MiB.
- e2e image override.
- Only the current version is served.
- Day-close tick queue.
- openapi_extra.
- zod binary resolver.
- Docs cited (DBOS listen_queues, python-multipart, python-magic, clamd INSTREAM, FastAPI openapi_extra, Hey API resolvers).

## PR 2 (`wp/P1-16-impl-2`), after #69

This covers T-06 (real Docling and `*.expected.yaml`), T-13 (the vision adapter; restore the parked test in
`handoff/P1-16/.../test_vision_contract.py`, and KEEP it there until then), A1.5 `knowledge_app` wiring,
`test_eicar_upload_is_quarantined` and `test_pdf_is_scanned_extracted_and_filed`.
