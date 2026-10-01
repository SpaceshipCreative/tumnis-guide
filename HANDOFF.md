# HANDOFF: P1-17 (Knowledge items, editor, search and passages), continuation c4

This file lives on `wp/P1-17-impl-2`. Scratch folder used by c4: `/tmp/claude-1002/P1-17-c4/`.

## State

| Item | State |
| --- | --- |
| PR #122 (`wp/P1-17`, backend) | MERGED (41fc850). Nothing left. |
| `wp/P1-17-impl-2` (PR 2, editor) | PR NOT opened yet. c4 commits: 4b952f4 (feat: editor, rail, preview; make check green), b5c1fe4 (test: BriefSection reads the editor, Scott decision 39; red on purpose until the brief moves), 002242d (merge main #120; TaskDrawer keeps Run + PacketToggle). |

Setup: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P1-17-impl-2`,
`cd backend && uv sync --frozen --all-extras`, `npm ci --prefix frontend`. Push `HEAD:wp/P1-17-impl-2`.

## Green (markers removed, c3 + c4)

T-P1-17-01, -03, -05 (c3); T-P1-17-02 useNoteSave, -04 makeTask, -16 PacketPreview, -17 KnowledgeSection (both),
-18 bundle (c4). No test.fails markers left in P1-17's Vitest specs.

## Open question to the coordinator (asked by SendMessage, waiting)

Moving the brief into the editor also breaks locked T-P0-24-15 (`rail/RightRail.test.tsx`): `toHaveValue(...)`,
`user.clear/type` on the textbox "Brief", and PATCH body `{ body_md: "Logo and site refresh", version: 1 }`.
Proposed: same mechanism as BriefSection.test (editor from `text.editor`; `editor.getMarkdown()` toBe the same
value; transactions for clear/type); (i) keep the PATCH value by stripping the final newline in **BriefEditor**
(not in MarkdownField; notes keep ADR-0008 canonical form), or (ii) expect "Logo and site refresh\n".

## Exact next steps

1. On the ruling: a separate `test(frontend): P0-24 T-P0-24-15 reads the editor (Scott decision 39)` commit for
   RightRail.test.tsx; then commit C = the brief move. The brief move is saved at
   `/tmp/claude-1002/P1-17-c4/BriefSection.tsx.new` (LazyMarkdownField label "Brief", onChange=setText); for
   option (i) strip one trailing "\n" in BriefEditor's onChange. Also uncommitted now: extensions.ts header comment
   (TrailingNode off) and a comment on KnowledgeSection's DELETE `kind: "create"`.
2. `bash /tmp/claude-1002/P1-17-c4/check.sh` must show 0 failures; push.
3. Delete HANDOFF.md (chore commit), push, open PR 2 (base main, title `[P1-17] impl: knowledge editor`) with
   body `/tmp/claude-1002/P1-17-c4/pr-body.md`; `gh pr comment <url> --body "@coderabbitai review"`; review loop.
4. CI green + 0 threads: SendMessage main `#<PR> MERGE-READY at <sha>`.

## Decisions and deviations (PR 2)

1. T-P1-17-02 asserts `PATCH` (the backend route), not the plan's "PUT".
2. Keystrokes in tests are editor transactions (jsdom has no layout for ProseMirror).
3. Mentions via `@tiptap/suggestion` (`MentionLinks`), no `@tiptap/extension-mention`.
4. `canEditSafely` compares marked token trees (no remark dependency).
5. Round-trip harness reads fixtures by path; `.skip-roundtrip` siblings filtered.
6. Vitest has two projects: `app` (jsdom, src/) and `build` (node, tests/; T-P1-17-18 builds the app).
   traceability.py only globs src/** for Vitest, so T-P1-17-18 in tests/ is outside its scan.
7. `editorMode(src)` in markdown.ts.
8. StarterKit `trailingNode: false` (T-P1-17-04 expects no trailing blank paragraph).
9. PacketPreview validates only `{ body }`: the locked T-P1-17-16 fixture is not a valid TaskPacket
   (`skill: "enrich_task"` fails `^[a-z][a-z0-9-]{0,40}$`; `timeout_s` missing). Scott item.
10. Uploads go through new `apiUpload` in lib/fetch.ts (REL-2 meta test: writes only via lib/fetch).
11. Test harness `src/test/nodeFile.ts`: Node File/Blob/FormData globals under jsdom (vitest-dev/vitest#11300,
    Node 24 undici builds multipart parts with the global File). Delete when Vitest ships the fix.
12. Knowledge count = quota.count minus the brief (listed in its own section).
13. Default MSW handlers (test/msw/handlers.ts) answer the Knowledge section's two reads.
14. P2-17 will make GET /v1/knowledge/search a page: Editor.tsx `apiSuggest` must read `.items` then.

## Scott items

- Open: `save_note` writes the note file before the DB commit (storage follow-up, CodeRabbit on #122).
- Open: T-P1-17-16 fixture vs TaskPacket schema (deviation 9).
- Resolved: A1.5 packet kind (decision 41), seed data (decision 37), brief editor (decision 39).

## Docs consulted (cite in PR body)

Tiptap via Context7 `/ueberdosis/tiptap-docs` (Markdown extension, list package, Suggestion, Link
`protocols`/`isAllowedUri`, StarterKit configure incl. `trailingNode: false`); marked `/markedjs/marked`;
Vitest 5 inline projects (Context7 `/vitest-dev/vitest`, migration guide: projects inherit root config);
vitest-dev/vitest#11300 (first-party PR); @tiptap/core sets `view.dom.editor` (package source).
