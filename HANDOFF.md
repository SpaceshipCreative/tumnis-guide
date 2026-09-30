# P3-14 handoff (Existing folders, shares and SFTP) - c2 -> c3

Two stacked PRs:

- **PR1 #107** `wp/P3-14` -> main (ready, CodeRabbit reviewed). Tip `aed18a1`.
- **PR2 #113** `wp/P3-14-impl-2` -> `wp/P3-14` (DRAFT, opened for CI; CodeRabbit skips drafts). This
  HANDOFF.md lives on wp/P3-14-impl-2 only.

## Branch mechanics (important)

Your worktree starts at main. Do: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`,
`/usr/bin/git merge origin/wp/P3-14-impl-2` (that includes wp/P3-14). Push PR2 work with
`/usr/bin/git push origin HEAD:wp/P3-14-impl-2`.

To land a fix on PR1 while HEAD holds PR2 commits (what c2 did, no checkout needed):
1. commit the fix on HEAD (commit F);
2. `/usr/bin/git merge-tree --write-tree --merge-base=F~1 origin/wp/P3-14 F` -> tree T;
3. `/usr/bin/git commit-tree T -p origin/wp/P3-14 -F <msg file>` -> F';
4. `/usr/bin/git push origin F':refs/heads/wp/P3-14`, then `/usr/bin/git merge origin/wp/P3-14` into HEAD.

When main moves: merge origin/main into HEAD, and bring PR1 up to date the same way (or ask the coordinator).
PR1 was CONFLICTING once (main's P2-18 touched knowledge/workflows.py): pull_request CI doesn't run
while a PR conflicts, so check `gh pr view <N> --json mergeable` if CI doesn't start.

## PR1 #107 state

- Commits since c1: 4d76733 (merge main), 25f6821 (markers off T-09..13, T-17, T-18: CI job
  110093669401 XPASS strict), 8784496 (feat: use_existing_folder, pulled from impl-2 to stop a hook
  leak), c665f9d (handoff removed), 6a8ef66 (merge main incl. #105 P2-18, #108; workflows.py conflict
  resolved keeping both), aed18a1 (CodeRabbit fixes).
- CodeRabbit: 5 comments, all fixed in aed18a1, replied and resolved; re-review requested, no new
  comments (review 5372441445 empty). 0 unresolved threads.
- CI on aed18a1: run 36781996519 is ALL GREEN (unit was cancelled once at its time limit and passed
  on rerun); preview is pending, as expected; GitGuardian is red (Scott item below). "#107 MERGE-READY at aed18a1"
  was SENT to main. Only redo this if #107 gets new commits.
- History: The earlier run (36779282233) had:
  integration parallel part all green (1211 passed) but the job CANCELLED at its 10-min budget
  in the serial step (the xfail T-16 kill test burnt 60 s waiting; PR2 makes it pass fast), and
  performance failed on Lighthouse total-blocking-time (runner noise; `gh run rerun --failed` once).
  If integration is cancelled again on PR1 only for time, rerun once; if it persists, report it.
- When CI is green: SendMessage to main "#107 MERGE-READY at <sha>".
- PR body is at $TMPDIR/P3-14-c2/pr-body.md (published). Update its "Test results" with the final run.

## PR2 #113 state (tip fbc1baf, CI run pending at handoff)

Built (commits c890005, fbc1baf and the markers commit):
- `api.py`: `_ExistingFolderGuard` wraps every backend of a location that holds existing-mode
  folders (write/move/delete refused outside `<root>/Tumnis/`; `allow_delete(path)` lets one
  user-confirmed delete through). `work_root(folder)`, `upload_dir(s, project)`; notes and uploads go
  under `Tumnis/` in existing mode. `rename_document` (title only). `delete_document` (may_delete:
  trash / index_only / 403 external_delete_forbidden), `record_delete_refused` (own transaction),
  `issue_delete_confirmation` / `delete_at_source` (table delete_confirmations, sha256 of a one-time
  token, 10 min, per user; compare_digest), `enqueue_move`.
- `sync.py`: `ignored(rel, mode)` also skips `Tumnis/.trash/`; notes placed under work root; conflict
  copies relocated under `Tumnis/` in existing folders; trash to `Tumnis/.trash/<path inside Tumnis>`;
  delete_at_source calls `allow_delete` first. `pipeline.place` uses `api.upload_dir`.
- `move.py` + workflow `knowledge_move_project_folder` (begin, list with hashes, copy batches of 100
  with kill point `knowledge.move_project_folder.batch_<n>`, verify, switch in one transaction,
  review kind `folder_move_old_copy` actions accept/snooze), `use_fault`.
- Routes: `POST /v1/knowledge/projects/{id}/existing-folder`, `POST /v1/knowledge/projects/{id}/folder/move`
  (202), `DELETE /v1/knowledge/documents/{id}` (session_or_key, knowledge:write, NOT idempotent: an
  idempotent write requires an Idempotency-Key header, which is why T-07/T-08 failed first),
  `POST .../delete-confirmation`, `POST .../delete-at-source` (202, session only).
- Migration `knowledge_0008` (down knowledge_0007): folder_moves, delete_confirmations.
- Markers off: T-06, T-14, T-15, T-16 (CI job 110110321802 XPASS strict).
- Still strict xfail: T-07, T-08 (should pass now after the DELETE-route fix; check CI, then remove
  markers at test_existing_folder.py lines ~161 and ~220 and commit citing the job id).
- Fixed after the first PR2 CI run: semgrep tumnis-secret-eq x2, A0.3 tenant sweep 422 on the
  existing-folder route (rows are now found before the path rule).

Remaining for PR2:
1. CI green (integration incl. A0.3 sweep and the authz/headers meta sweeps for the new routes).
2. Frontend (plan): `frontend/src/components/project/FolderSetup.tsx` (existing-folder form: location
   + path; hint that Tumnis writes only in Tumnis/), a move dialog (location + path -> POST move,
   show folder_moves outcome via the review item), a delete-at-source confirmation dialog (issue token,
   typed confirmation + reason). No spec test is planned for them; add small Vitest tests.
   Run `make gen` after any backend route change.
3. Not done / deviations to list: `folder_move_old_copy` offers accept/snooze only (the plan's
   `reject` = remove the old copy after typed confirmation is not built: it deletes files at a source
   and needs a human.decided subscriber in knowledge); the move keeps the folder mode; P2-18's
   archive writes `.tumnis/archives` even in an existing folder (the guard would refuse it; P2-18
   archives only the index for existing folders per plan, verify).
4. Write the PR2 body (template: $TMPDIR/P3-14-c2/pr-body.md), `gh pr ready 113`, one
   `@coderabbitai review`, review loop, MERGE-READY for #113 after #107 merges (rebase base to main
   then: `gh pr edit 113 --base main` once #107 is merged).
5. Delete this HANDOFF.md in a chore commit on wp/P3-14-impl-2 when done.

## Scott items (carry forward)

- GitGuardian incident 37763317 ("Generic Password", test_sftp_auth.py, commits 1328484/2de262b):
  test prompt text, false positive; mark on the dashboard. No history rewrite.
- Classifier denial (c2): a read-only grep of `tests/integration/_folder_runner.py` was denied as
  "[Security Test Removal]" right after approved marker removals; c2 left the harness alone.
- `_existing()` in test_existing_folder.py starts the runner outside try/finally (a failing start()
  leaks sync hooks into later tests); a spec PR could move it inside the try.

## Local constraints

- make check: `bash $TMPDIR/P3-14-c2/check.sh` (copy it to your own folder; it sets semgrep env).
- CI wait: `bash $TMPDIR/P3-14-c2/waitci.sh <sha-prefix> <branch>` (run_in_background).
- Threads: `threads.sh <PR>`, `reply.sh <PR> <comment id> <text> <thread id>` in $TMPDIR/P3-14-c2/.
- Docker tests: CI only (coordinator: at most one local `make test-int` per continuation).
- The worktree guard refuses compound commands that mention git-like text; use Write + python3 for edits.
