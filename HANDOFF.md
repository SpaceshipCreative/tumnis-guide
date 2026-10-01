# HANDOFF: P1-17 PR 2 (#131, knowledge editor), written by c4 on "HANDOFF NOW"

Scratch folder used by c4: `/tmp/claude-1002/P1-17-c4/`. Use your own folder (`P1-17-c5`) and copy the
helper scripts across: `threads.py`, `reply.py`, `watch_all.sh` and `check.sh`. Edit check.sh's `cd` line
for your worktree.

## State

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/131, title `[P1-17] impl: knowledge editor`, base main.
  - The coordinator has added the `spec-change` label.
  - CodeRabbit has reviewed once (on 9c2f8af). Don't request another full review.
- Branch `wp/P1-17-impl-2`. Commits in c4:
  - 4b952f4 feat: note editor, Knowledge section and packet preview.
  - b5c1fe4 test: BriefSection reads the editor (decision 39).
  - 002242d merge main (#120).
  - fcf6546 docs: code comments.
  - ca213b3 test: T-P0-24-15 in RightRail.test.tsx reads the brief editor (decisions 39 and 50, option (i)).
  - ab80ab9 feat: brief in the editor; Save sends it without the final newline.
  - 143ab40 chore: removed the old handoff.
  - 9c2f8af merge main (#127).
  - a9d5452 fix: the 4 CodeRabbit findings.
  - This handoff commit.
- `make check` was green at a9d5452: unit 1745, Vitest 113 files and 284 tests.
- Local spec-guard reports only the 2 approved `edited_test` entries.

## CI on 9c2f8af (run 36826476244)

- Everything passed except `performance`. That job was cancelled at its 5-minute budget during the
  "Lighthouse CI, Core Web Vitals at 375 px on LAN" step.
- Every earlier step passed: bundle 186.6 KB of the 200 KB budget, k6 0% failed.
- It looks like runner time variance. If it recurs on the new push, re-run it once with
  `gh run rerun <run-id> --failed`. Don't edit ci.yml.
- `preview` stays pending (no homelab runner).

## CodeRabbit threads: 4 open, all fixed in a9d5452, NOT yet replied to or resolved

Reply to each, then resolve it: `python3 threads.py 131` lists them, and `python3 reply.py <id> "<body>"` replies and resolves.

- PRRT_kwDOUx-vCc6n1YoB (TaskDrawer: key PacketToggle by task id): fixed in a9d5452.
- PRRT_kwDOUx-vCc6n1YoM (KnowledgeSection: stale list cache after a note save): fixed in a9d5452. `onSaved` writes
  the saved DTO into the list cache (via `knowledgeListDocumentsOptions(listArgs).queryKey`), and the editor is keyed by doc.id.
- PRRT_kwDOUx-vCc6n1YoS (Editor: unhandled makeTask rejection): fixed in a9d5452. It now shows a `role="alert"`
  saying "Could not make the task. Try again."
- PRRT_kwDOUx-vCc6n1YoZ (useNoteSave: a source-mode note compared with its round trip, so the save was skipped): fixed in a9d5452.
  The baseline is `loaded` when `editorMode(loaded) === "source"`.

## Exact remaining steps

1. Setup:
   - `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P1-17-impl-2`.
   - `cd backend && uv sync --frozen --all-extras`, then `npm ci --prefix frontend`.
   - If main changed the generated API files, run `make gen` and don't hand-merge them.
2. Reply to and resolve the 4 threads above.
3. Wait for CI on the new head with `watch_all.sh 131 <sha7>`, then read any new CodeRabbit threads and fix or answer them.
4. When CI is green (`preview` pending is fine) and there are no open threads, send `#131 MERGE-READY at <sha>`
   to main with SendMessage.
5. Delete HANDOFF.md in a `chore` commit before sending MERGE-READY. Push with `/usr/bin/git push origin HEAD:wp/P1-17-impl-2`.

## Decisions and deviations (also in the PR body)

1. T-P1-17-02 asserts PATCH, the backend route.
2. Keystrokes in tests are editor transactions, because jsdom has no layout for ProseMirror.
3. Mentions use `@tiptap/suggestion` in `MentionLinks`. No Mention node.
4. `canEditSafely` compares marked token trees.
5. The round-trip fixtures are read by path, and `.skip-roundtrip` siblings are skipped.
6. Vitest has two projects: `app` (jsdom, src/) and `build` (node, tests/). traceability.py doesn't scan tests/.
7. `editorMode()` was added to markdown.ts.
8. StarterKit's `trailingNode` is off.
9. PacketPreview validates only `{ body }`. The T-P1-17-16 fixture isn't a valid TaskPacket. The coordinator
   says don't change the fixture; it stays a Scott item.
10. Uploads go through `apiUpload` in lib/fetch.ts (the REL-2 meta test).
11. `src/test/nodeFile.ts` makes Node's File, Blob and FormData global under jsdom (vitest-dev/vitest#11300).
12. The Knowledge count is the quota count minus the brief.
13. P2-17 will change knowledge search to a page. `apiSuggest` in Editor.tsx must then read `.items`.
14. Spec changes (decisions 39 and 50):
    - BriefSection.test.tsx and RightRail.test.tsx T-P0-24-15 drive the editor.
    - The brief saves without the final newline (decision 50, option (i)).

## Scott items

- T-P1-17-16 fixture versus the TaskPacket schema.
- `save_note` writes the file before the DB commit (storage follow-up).

## Verify

- `bash /tmp/claude-1002/P1-17-c4/check.sh` (copy it and edit the `cd` line).
- `cd frontend && npx vitest run src/editor src/components/project tests/`
- Spec guard: `cd backend && uv run python ../scripts/ci/spec_guard.py --base origin/main --head HEAD --labels ""`
  should list only the 2 approved edits.
