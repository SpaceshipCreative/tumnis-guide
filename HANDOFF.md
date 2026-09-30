# HANDOFF: P1-16 Upload safety and extraction (fifth handoff, c4)

Impl PR **#69** (https://github.com/SpaceshipCreative/tumnis-guide/pull/69), branch `wp/P1-16`.
Spec PR **#83** (https://github.com/SpaceshipCreative/tumnis-guide/pull/83), branch `wp/P1-16-spec-t03-bound`,
base `wp/P1-16`, head 7bbc512 (parent 0b8216b). It holds decision 21's T-03 bound (`+ 2 * MIB`) and the matching
docstring sentence. There's no label; the coordinator adds `spec-change`. Its spec-guard is red until then (expected).

Setup: throwaway branch at main in a worktree, then `/usr/bin/git fetch origin` and
`/usr/bin/git merge origin/wp/P1-16`. Push only with `/usr/bin/git push origin HEAD:wp/P1-16`.
Read `~/tumnis-coordinator/vm-agent-rules.md` and `scott-decisions.md` (items 10, 11, 12, 14 and 21 are P1-16's).

## Done in c4 (all pushed to wp/P1-16)

- 9371b81: merged main 45a4f00 (P1-15, P2-14).
  - The migration is now `knowledge_0006` (`0006_extraction.py`, down `knowledge_0005`); one knowledge head.
  - The name rules have one copy in `rules.py` (`sanitize_filename`, `numbered_name` and `split_ext` moved from `sync_rules.py`, which re-exports them via `__all__`); `upload_file_name` = basename + `sanitize_filename`.
  - worker: folder watch runs in the main worker only; both configure calls run.
  - `coolify-poll` runs on the `sync` queue.
  - `workflows.py`: extraction retry is `EXTRACT_STEP_RETRY`.
- 0b8216b: merged main (#72).
- 10c6f2a: the spool directory is made only when the file part starts. This fixes the schema-fuzz 500 (PermissionError on `/var/lib/tumnis`); 2 unit tests in `test_review_fixes.py`.
- 4d503ed: merged main fa5da78 (P2-13). `github` queue kept; `main_queues()` includes `github`; `make gen`.
- 949cb27: FakeDocling `_PAGE_PNG` is a valid PNG (CodeRabbit), with `test_fake_page_image.py`. Thread replied and resolved.
- #83: CodeRabbit asked for a tighter bound (`MAX + MIB + head bytes`). Declined, citing decision 21; thread resolved. This is a Scott item.
- Local checks after 4d503ed:
  - ruff, format, mypy and lint-imports are clean.
  - Unit: 1317 passed; daemon and profiles pass; node check_bundle passes.
  - Frontend typecheck and lint are clean. Vitest has load-only timeouts: different tests fail on each run, and TaskDrawer comes from P2-13.
- First local test-int (at 0b8216b): 6 failed, 994 passed.
  - The fuzz 500 is fixed in 10c6f2a.
  - T-03 showed XPASS(strict) only because I edited the bound to +2 MiB for #83 while the run was collecting. That is the only evidence T-03 passes at +2 MiB.
  - The rest are load or env flakes: rclone, relay notify, typeahead latency, and csrf-sweep (a ResourceWarning from a leaked subprocess).
- Second test-int (at 4d503ed/949cb27) was running when this was written: check that the fuzz passes and T-03 is a plain XFAIL.

## NEXT STEPS

1. Check CI on #69 at 949cb27 or later: unit (schedule-queue guard), integration (fuzz, migrations), contract.
2. Update the #69 body: a prepared version is at `/tmp/claude-1002/P1-16-c4/body69-new.md`. Fill in its "Test results" section with the c4 results, then run `gh pr edit 69 --body-file ...`.
3. When the coordinator merges #83 into wp/P1-16:
   - Fetch and merge `origin/wp/P1-16`.
   - Remove T-03's `xfail(strict=True)` in `test_upload_safety.py`.
   - Push, and confirm CI integration passes T-03 at that commit. Remove the marker only if it passes.
4. `@coderabbitai review` on #69 after pushes; answer any threads.
5. When #69 is clean, delete this file in a `chore:` commit.

## Scott items (keep)

- Decision 21 / #83: CodeRabbit's point stands. A 51 MiB file can't tell early refusal from reading the whole body, except by the closing tail. Neither bound closes that gap. A possible follow-up spec change: `MAX + MIB + head bytes`, or a bigger file.
- P1-15 folder files skip the scan. `place_upload` and `create_file_document` insert documents that get `status='ready'` from knowledge_0006's default, so `GET /v1/files/{id}` would serve them unscanned. P1-15's extraction-hook follow-up (`sync.request_extraction` -> `knowledge_extract_document`) should create them `pending_scan`.
- P1-15's `0005_folder_files.py` docstring still says "re-chained after P1-16's knowledge_0004". It is stale; I left it untouched, as instructed.
- Still open: Docling in CI for impl-2 (CPU torch wheels, prefetched models; decision 8 covers the budget).
- Housekeeping: stale worktree entry `.git/worktrees/p116-spec-eicar`.

## Deviations (c4, beyond those already in the #69 body)

- #83 also updates T-03's docstring sentence to match the new bound. Coordinator note (e) said the bound only; this is explained in #83's body.
- The folder watcher does not run in `worker-extract`.
- `coolify-poll` uses the `sync` queue.
- Shared name rules live in `rules.py`.

## PR 2 (`wp/P1-16-impl-2`), after #69

- T-06: real Docling and `*.expected.yaml`.
- T-13: the vision adapter. Keep the parked test at `handoff/P1-16/.../test_vision_contract.py` until then.
- A1.5: `knowledge_app` wiring, `test_eicar_upload_is_quarantined` and `test_pdf_is_scanned_extracted_and_filed`.
