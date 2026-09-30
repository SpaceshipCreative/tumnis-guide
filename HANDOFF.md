# HANDOFF: P1-15-scan (#99, PR #100)

Task prompt: /tmp/claude-1002/coord7/p1-15.txt. Branch `fix/P1-15-folder-scan` (local branch of the same name in worktree agent-ac8bb74e29309fe10). Push with `/usr/bin/git push origin HEAD:fix/P1-15-folder-scan`. Scratch: /tmp/claude-1002/P1-15-scan-c0/ (pr-body.md, reply1.md, wait_ci.sh, wait_review.sh, int1.log, int2.log).

## PR and issue

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/100 (label `bug`, body follows the PR template, "Fixes #99").
- Issue #99, which I filed so red-proof has an issue number (tests are `test_issue_99_*`).

## Commits (on origin/main 12956ea)

- 256e109 test(knowledge): folder uploads must be scanned (red)
- a78a765 test(knowledge): placed uploads recorded, sniffed text is no note (red)
- d7df2ba fix(knowledge): folder files go through the malware scan (#99)
- 5671417 test(knowledge): a never-released folder file is not served (red)
- b02f02a fix(knowledge): serve a file only once the pipeline released a version (#99) (pushed)
- plus this handoff commit

## State

- CI on d7df2ba: all green (red-proof, integration, contract, unit, e2e, security, spec-guard, traceability, CodeRabbit); `preview` pending (homelab runner, allowed by merge-pr.sh).
- CI on b02f02a: was running at handoff; `bash /tmp/claude-1002/P1-15-scan-c0/wait_ci.sh` waits for it. If `contract` shows cancelled, rerun once with `gh run rerun <run-id> --failed`.
- CodeRabbit: first pass (on d7df2ba) posted no inline comments and 0 review threads, so nothing to resolve. Its summary raised 2 security-architecture concerns and 2 pre-merge warnings; all answered in the PR comment from reply1.md, which also asked `@coderabbitai review` for b02f02a:
  1. Legacy `ready` rows reachable after the path fix: FIXED (5671417 red, b02f02a green; `download_info` refuses a document with no `current_version_id`).
  2. TOCTOU between the scanned bytes and the streamed bytes, plus the `pipeline._fetch` refetch without a digest check: pre-existing, shared with P1-16 route uploads. Declined here and listed as a Scott item (decision-10 design).
  3. Docstring coverage warning: helper docstrings added. Description-template warning: body restructured to the template.
- merge-pr.sh counts a passing "CodeRabbit" check as a review, and needs 0 unresolved CodeRabbit threads.

## Remaining steps

1. Wait for CI on b02f02a to go green (`wait_ci.sh`), and for CodeRabbit's re-review (`bash /tmp/claude-1002/P1-15-scan-c0/wait_review.sh 1` counts formal reviews only; also read `gh api repos/SpaceshipCreative/tumnis-guide/issues/100/comments` for a new summary, and `.../pulls/100/comments` for inline ones).
2. Handle any new CodeRabbit comments with the pr-review-loop (fix with TDD, or reply with a reason; resolve threads through GraphQL `resolveReviewThread`).
3. Remove this HANDOFF.md in a `chore:` commit once work resumes (or the coordinator does it before merge).
4. When CI is green and there are 0 unresolved threads, SendMessage to main: `#100 MERGE-READY at <sha>`. Never merge.

## What the fix does

- `create_file_document` / `api.add_version`: document and version start `pending_scan` (fail-closed default; only a note's own text is `ready`). `documents.path` is folder-relative (`folder_rel_path`), as the pipeline and `download_info` read it; an outside rename updates it (file docs only).
- Folder sync default extraction hook = `workflows.enqueue_folder_extraction` (DBOS enqueue of `knowledge_extract_document`, source `storage`, id `extract:<version_id>`, from a fresh contextvars context like `agents.workflows.start_provision`), registered with `sync.register_extraction`. Conflict copies (rows 9/11) are extracted too. `place_upload` returns `version_id`; its caller enqueues after commit.
- `api.is_note` (kind AND source `text`) replaces every `kind == "text"` note check in sync.py and `update_text_document` (kind collision with P1-16's sniffed `text`; coordinator approved).
- `pipeline.place` records the placed upload in `folder_files` (`api.record_placed`) so the next folder sync makes no second document (coordinator asked for it).
- `download_info` refuses a document with no released version.
- knowledge_0005 docstring: the chain is 0003 -> 0005 -> 0006.
- Part A plan row (P1-16) lists the new names.

## Test results

- `make check` green locally. Semgrep needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` set to files under /tmp/claude-1002/P1-15-scan-c0/.
- `make test-int` locally (on d7df2ba, heavy load): 1104 passed, 6 failed, all outside knowledge and load-bound (test_relay timing x3 (#49), rclone known local-only, typeahead latency, calendar reconnect). Every knowledge test passed. CI integration green.

## Scott items

- TOCTOU on `GET /v1/files` (serve from an immutable copy, or hash while streaming), and a digest check on the scratch refetch.
- `place_upload` writes before the scan (the names_sanitized scenario needs that), so an infected file placed that way stays in the folder, quarantined. It has no production caller.
- Folder files keep the stand-in `documents.body_md` before the scan (fixtures assert it). P1-17 readers should use released versions.
