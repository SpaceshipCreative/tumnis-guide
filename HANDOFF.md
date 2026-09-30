# HANDOFF: P1-16 Upload safety and extraction (fifth handoff, end of c4)

Impl PR **#69** (https://github.com/SpaceshipCreative/tumnis-guide/pull/69), branch `wp/P1-16`, head **14e15b5**.
Spec PR **#83** (https://github.com/SpaceshipCreative/tumnis-guide/pull/83), branch `wp/P1-16-spec-t03-bound`,
base `wp/P1-16`, head 7bbc512 (parent 0b8216b). It holds decision 21's T-03 bound (`+ 2 * MIB`) and the matching
docstring sentence. The coordinator has added `spec-change`. #83 was OPEN, not merged, at handoff.

Setup: throwaway branch at main in a worktree, then `/usr/bin/git fetch origin` and
`/usr/bin/git merge origin/wp/P1-16` (then `origin/main`). Push only with `/usr/bin/git push origin HEAD:wp/P1-16`.
Read `~/tumnis-coordinator/vm-agent-rules.md` and `scott-decisions.md` (items 10, 11, 12, 14 and 21 are P1-16's).
Sandbox gotchas:
- Run one plain `/usr/bin/git` command per call: no `$TMPDIR` or `cd X &&` next to git.
- `make check` needs `SEMGREP_*` env vars under /tmp/claude-1002/P1-16-c4/sg.
- CI logs: `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs` with allowed_domains `*.blob.core.windows.net`.
- GitHub GraphQL often 502s. Use the REST `commits/<sha>/check-runs` endpoint instead; `/tmp/claude-1002/P1-16-c4/wait_ci.sh <sha>` polls it.

## Commits this round (c4), all on wp/P1-16

- 9371b81: merged main 45a4f00 (P1-15, P2-14).
  - Migration renumbered to `knowledge_0006` (`0006_extraction.py`, down `knowledge_0005`); one knowledge head.
  - One copy of the name rules in `rules.py`. `sanitize_filename`, `numbered_name` and `split_ext` moved from `sync_rules.py`, which re-exports them via `__all__`. `upload_file_name` = basename + `sanitize_filename`.
  - Worker: the folder watch runs in the main worker only; both configure calls run.
  - `coolify-poll` runs on the `sync` queue.
  - Extraction retry is `EXTRACT_STEP_RETRY`.
- 0b8216b: merged main (#72).
- 10c6f2a: the spool directory is made only when the file part starts. This fixes the schema-fuzz 500; 2 unit tests in `test_review_fixes.py`.
- 4d503ed: merged main fa5da78 (P2-13). The `github` queue is kept and is in `main_queues()`; `make gen`.
- 949cb27: FakeDocling `_PAGE_PNG` is a valid PNG (from CodeRabbit), with `test_fake_page_image.py`.
- dc0c23f: HANDOFF update.
- 8b7d3be: `knowledge_0006` passes squawk. Both status checks are NOT VALID, and `document_versions.body_md` stays NOT NULL: `store.add_version` writes `''`, the model is `Mapped[str]`, and the plan's Part A row matches. The squawk step had been hidden behind the red EICAR pytest step.
- 4d1e0a3: merged main 79909cc (P0-25, P2-07); clean.
- 5ffe7cb: `_add_version` in `test_file_serving_versions.py` (our review test, not a spec test) now inserts `body_md ''`. CI integration at 4d1e0a3 failed on its two tests with NotNullViolation.
- 14e15b5: the status checks are written as literal SQL. CI security at 4d1e0a3 failed on semgrep `tumnis-sql-fstring` for the f-string helper.

## CI state

- At 4d1e0a3, green: unit, contract (squawk passes now), e2e, lint, spec-guard, traceability, red-proof, daemon, skills, performance and version-skew.
  - The fuzz passes and T-03 is a plain xfail.
- At 4d1e0a3, red:
  - integration: the 2 NotNullViolation tests, fixed in 5ffe7cb.
  - security: semgrep, fixed in 14e15b5.
- `preview` stays queued (no runner).
- **14e15b5 has NOT been pushed yet if this file's commit isn't on origin. Check with `/usr/bin/git log origin/wp/P1-16 -3`.**
- Locally at 14e15b5: squawk passes, the semgrep custom rules report 0 findings on knowledge/, and ruff passes.
- Locally at 4d1e0a3: mypy is clean and unit has 1332 passed.

## Review threads

- #69: every thread is resolved. The latest, the PNG thread, was fixed in 949cb27 and replied to. `@coderabbitai review` was requested after the body update; check it for new comments.
- #83: one thread, where CodeRabbit asked for a tighter bound (`MAX + MIB + head bytes`). I declined, citing decision 21, and resolved it. This is a Scott item.
- #69 body: updated in c4, covering the merges, the squawk fix, test results and Scott items. Its local copy is `/tmp/claude-1002/P1-16-c4/body69-new.md`. Add 5ffe7cb and 14e15b5 to it.

## NEXT STEPS

1. Push if needed, then wait for CI at the new head. Integration and security should now be green.
2. Add 5ffe7cb and 14e15b5 to the #69 body (the "Merges of main in this round" list), then run `gh pr edit 69 --body-file ...`.
3. When the coordinator merges #83 into wp/P1-16:
   - Confirm with `/usr/bin/git merge-base --is-ancestor 7bbc512 origin/wp/P1-16`, then merge `origin/wp/P1-16`.
   - Remove T-03's `@pytest.mark.xfail(strict=True, reason="spec:P1-16")` in `backend/tumnis/modules/knowledge/tests/integration/test_upload_safety.py::test_51_mb_refused_50_mb_accepted`.
   - Push, and keep the marker off only if CI integration passes T-03.
4. `@coderabbitai review` after pushes; answer any threads.
5. When #69 is clean (CI green except preview, 0 unresolved threads), delete this file in a `chore:` commit and write the final report.

## Scott items (keep)

- **Decision 21 / #83:** CodeRabbit's point stands. A 51 MiB file can't tell early refusal from reading the whole body, except by the closing tail. Neither bound closes that gap. A possible follow-up spec change: `MAX + MIB + head bytes`, or a bigger file.
- **P1-15 folder files skip the scan.** `place_upload` and `create_file_document` insert documents that get `status='ready'` from knowledge_0006's default, so `GET /v1/files/{id}` would serve them unscanned. P1-15's extraction-hook follow-up (`sync.request_extraction` -> `knowledge_extract_document`) should create them as `pending_scan`.
- **Stale docstring.** P1-15's `0005_folder_files.py` still says "re-chained after P1-16's knowledge_0004". I left it untouched, as instructed.
- **Still open:** Docling in CI for impl-2 (CPU torch wheels, prefetched models; decision 8 covers the budget).
- **Housekeeping:** stale worktree entry `.git/worktrees/p116-spec-eicar`.

## Deviations (c4, beyond those already in the #69 body)

- #83 also changes T-03's docstring sentence to match the new bound. Coordinator note (e) said the bound only; #83's body explains this.
- The folder watcher doesn't run in `worker-extract`.
- `coolify-poll` is on the `sync` queue.
- The name rules are shared in `rules.py`.
- `document_versions.body_md` stays NOT NULL (`''` until extracted) instead of becoming nullable, to satisfy squawk's ban-drop-not-null and keep the change expand-safe.
- Both status checks are NOT VALID.
- The spool directory is made lazily.

## Verify commands

- `cd backend && uv run alembic heads` shows exactly one `knowledge_0006 (knowledge) (head)`.
- `cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main` reports `passed ... knowledge_0006`.
- Run `make check` with the SEMGREP env vars. Vitest times out under VM load; run the failing file alone.
- `make test` and `make test-int` must run bare. Integration on the VM often hits Docker 500s under load, so rely on CI for that layer.

## PR 2 (`wp/P1-16-impl-2`), after #69

- T-06: real Docling and `*.expected.yaml`.
- T-13: the vision adapter. Keep the parked test in `handoff/P1-16/.../test_vision_contract.py` until then.
- A1.5: `knowledge_app` wiring, `test_eicar_upload_is_quarantined` and `test_pdf_is_scanned_extracted_and_filed`.
