# HANDOFF: P1-17 (Knowledge items, editor, search and passages), continuation c3

Written on "HANDOFF NOW" from the context watcher. This file lives on `wp/P1-17-impl-2`.
Scratch folder used by c3: `$TMPDIR/P1-17-c3/` (= /tmp/claude-1002/P1-17-c3/).

## State

| Item | State |
| --- | --- |
| PR #122 (`wp/P1-17`, backend) | **MERGED** into main on 2026-10-01 as 41fc850 (head was 3bd599a). Nothing left to do there. |
| `wp/P1-17-impl-2` (PR 2, editor) | NOT opened yet. Head = this handoff commit. Based on 3bd599a (= #122's final head, which includes main up to #125/#126) + c2's red spec commit c637274 + c3's c56b5a2. |

Setup for c4 (throwaway branch at main): `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`,
`/usr/bin/git merge origin/wp/P1-17-impl-2`; resolve generated files with `make gen` (never
hand-merge them); then `cd backend && uv sync --frozen --all-extras` and `npm ci --prefix frontend`.
Push with `/usr/bin/git push origin HEAD:wp/P1-17-impl-2`. Since #122 is merged, open PR 2 with
**base main**: title `[P1-17] impl: knowledge editor`. Request CodeRabbit once when it opens.

## What c3 did on #122 (for the record; all merged)

- 293b3d1 / 3bd599a: merged main (P1-11 #119; then #125, #126). For the 405 `Allow`, main's
  concrete-before-templated order is combined with P1-17's `declines(path_params)` hook in
  `core/errors.py`.
- ae0e6d1 + ac4d76c + 9cc8bc0: `purge_trash` uses `FOR UPDATE SKIP LOCKED` (purge vs restore race);
  integration test `test_purge_restore.py` with a control step.
- 4240a7c: `announce()`: a workspace knowledge item (no project) sends a `knowledge` live message;
  LIVE_MAP has a `knowledge` entity (lists, quota, search). Unit test `test_announce.py`.
- a551b49: `list_documents` serves items through `_dto` (release gate). `test_list_release_gate.py`.
- 5810283: REST text writes carry caller taint (`agent_surface.caller_tainted`); `POST .../trust`
  takes an optional `version` (409 `stale_version` if edited since; optional because locked
  T-P1-17-07 posts without it). Tests `test_text_taint.py`, `test_trust_version.py`.
- 545dd8a: `GET .../versions` -> `api.released_versions` (a file version's body only when the
  version and the document are both `ready`; `list_document_versions` stays raw for T-P1-15-10).
- Replied on the PR about CodeRabbit's architecture notes. Two inferred notes stay open: the enrich
  preview exposing passages without `context:read` (the ordinary packet gives a tasks:read caller
  passages too; only context items are behind context:read), and the optional trust version.
  The note-file export before commit belongs to P1-14/P1-15's storage layer, so it's a follow-up.

## PR 2 progress (c3)

c56b5a2 `feat(frontend): editor Markdown round trip, link safety and mention links`:
`frontend/src/editor/{markdown,extensions,links,slash}.ts` from c2's drafts, type-fixed
(`Suggestion<X, X>`, `reportMenu` returns `NonNullable<SuggestionOptions<T, T>["render"]>`).
**Green, markers removed:** T-P1-17-01 (roundtrip.test.ts, 45 tests), T-P1-17-05 (markdown.test.ts),
T-P1-17-03 (links.test.ts). `make check` green at c56b5a2 (Vitest 273 passed, 6 expected fail).

**Still red (strict markers on):** T-P1-17-02 (useNoteSave.test.tsx), T-P1-17-04 (makeTask.test.ts),
T-P1-17-16 (PacketPreview.test.tsx), T-P1-17-17 (KnowledgeSection.test.tsx, 2 tests),
T-P1-17-18 (tests/bundle.test.ts).

**Drafts ready to copy (NOT yet run)** in `/tmp/claude-1002/P1-17-c3/impl/src/`:
- `editor/useNoteSave.ts`, `editor/makeTask.ts`, `editor/Editor.tsx` (c2's drafts),
  `editor/LazyEditor.tsx` (c3: `React.lazy(() => import("./Editor"))` with Suspense).
- `components/project/rail/KnowledgeSection.tsx` (c3): summary `"N items · X MB of Y GB"` from
  `knowledgeGetQuotaOptions({query:{project_id}})` (`count`, `used_bytes`, `quota_bytes`; 1024-based
  `formatBytes`); list from `knowledgeListDocumentsOptions`; "Add text" (Title/Text, Add), "Add link"
  (Link, Add; body exactly `{project_id, url}`), file input labelled "Upload a file" via the generated
  `knowledgeUploadDocument` with Idempotency-Key and X-CSRF-Token headers (apiFetch would force JSON
  Content-Type); Pin/Unpin `PATCH {pinned, version}`; "Move <title> to trash" DELETE then an inline
  "Undo" that POSTs `/restore`; text items open `LazyEditor`. Only one add form open at a time (the test
  clicks the single "Add" button).
- `components/project/PacketPreview.tsx` (c3): `<section aria-label="Packet preview">`,
  `agentsGetTaskPacketOptions({path:{task_id}, query:{kind:"enrich"}})`, brief first, then passages cited
  `"Title, page N"`.

## Exact next steps

1. Copy useNoteSave.ts + makeTask.ts, run `npx vitest run src/editor` (from frontend/), make T-02 and
   T-04 green, remove their `test.fails` markers one at a time, `make check`, commit.
2. Copy Editor.tsx + LazyEditor.tsx (Editor props: `document`, `projectId`, `label?`, `suggest?`,
   `onSaved?`, `onOpenLink?`, `onReady?`); typecheck.
3. Copy PacketPreview.tsx; make T-16 green; add a "Preview packet" toggle to
   `components/project/drawer/TaskDrawer.tsx` (after `<PullRequests>`) that renders it.
4. Copy KnowledgeSection.tsx; wire it into `rail/RailSections.tsx` (Section union gains
   "knowledge"); add a quota handler and an empty documents list to `ProjectFake`
   (`frontend/src/test/msw/project.ts`) so the locked rail tests keep every request handled
   (MSW `onUnhandledRequest: "error"`); make T-17 (both tests) green.
5. T-18 bundle test (~30 s): Tiptap must not be in the initial chunk graph; only `LazyEditor`
   imports `./Editor`. Make it green.
6. The editor's trust UI (if any) should send `version` to `POST .../trust` (5810283).
7. `make check`, `npm --prefix frontend run typecheck`, Vitest; Playwright A1.5 needs Docling
   and seed data (Scott items), so it stays marked.
8. Delete HANDOFF.md in a `chore` commit, push, then open PR 2 (base main) with a body listing
   per-layer results, shared-file edits (TaskDrawer, RailSections, msw/project.ts, live-map),
   deviations (below), docs consulted (below), and the Scott items. End the body with a blank
   line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Then
   `gh pr comment <url> --body "@coderabbitai review"`, and run the review loop.
9. When CI is green with 0 open threads, SendMessage to "main": `#<PR> MERGE-READY at <sha>`.

## Decisions and deviations (PR 2)

1. T-P1-17-02 asserts `PATCH` (the backend route, R-36 / PR 1), not the plan's "PUT".
2. Keystrokes in tests are editor transactions (jsdom has no layout for ProseMirror).
3. Mentions go through `@tiptap/suggestion` in a `MentionLinks` extension, not `Mention.configure`
   (no Mention node or mention Markdown syntax is registered). `@tiptap/extension-mention` is not added.
4. `canEditSafely` compares marked token trees (marked is already @tiptap/markdown's parser),
   not mdast (no remark dependency added).
5. The round-trip harness reads fixtures by filesystem path (`fileURLToPath`); `.skip-roundtrip`
   siblings are filtered out.
6. `vitest.config.ts` and `tsconfig.json` include `tests/` (the plan's path for T-18).
7. `editorMode(src)` was added to markdown.ts so T-05 can check source mode without rendering.
8. The brief stays in its textarea pending Scott (item 3). BriefSection and its test are untouched (coordinator instruction).

## Scott items (unchanged from c2)

1. **A1.5 step 4 vs T-P2-02-14 (both locked).** A1.5 calls `GET /v1/tasks/{id}/packet` with no
   `kind` and expects `enrich`; T-P2-02-14 expects the default `task`. #122 kept default `task` and
   added `kind=enrich`. Options: (a) A1.5 passes `kind=enrich`, (b) a session defaults to enrich.
2. A1.5 integration steps 1-4 and Playwright A1.5 need real Docling in CI and an `Acme site` seed
   project (no seed data from us).
3. **Brief into the editor vs the locked `rail/BriefSection.test.tsx`** (`toHaveValue` on the textbox
   "Brief", `user.type`) can't work on a ProseMirror contenteditable. Options: (a) a spec-change PR
   adapts that test, (b) keep the textarea for now (current choice, deviation 8), (c) the textarea stays
   as the source view and the editor moves to a separate screen.
4. (new, c3) Follow-up for the storage owner: `save_note` writes the note file before the DB commit.
   A rollback afterwards leaves the file visible without a committed version (CodeRabbit inferred).

## Docs consulted (cite in the PR 2 body)

- Tiptap via Context7 `/ueberdosis/tiptap-docs`: the Markdown extension (`contentType: 'markdown'`,
  `getMarkdown`), the v3 list package, Suggestion render hooks, Link `protocols` / `isAllowedUri`
  (re-checked by c3), and the 2025-06-25 link popover XSS incident note (validation via setLink/isAllowedUri).
- marked via Context7 `/markedjs/marked` (Lexer, token fields).
- #122 extras (c3): SQLAlchemy `with_for_update(skip_locked=True)` (Context7
  `/websites/sqlalchemy_en_20_core`); PostgreSQL 18 SELECT locking clause:
  https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE

## Verify commands

- `bash /tmp/claude-1002/P1-17-c3/check.sh` (make check with the semgrep files under $TMPDIR; it `cd`s into
  this worktree, so edit its `cd` line for a new one)
- `cd frontend && npx vitest run src/editor src/components/project/PacketPreview.test.tsx src/components/project/rail tests/`
- `bash /tmp/claude-1002/P1-17-c3/watch_all.sh <PR> <sha7>` (background): waits for CI and CodeRabbit
- `python3 /tmp/claude-1002/P1-17-c3/threads.py <PR>` (open threads); `reply.py <thread> <body>` replies and resolves
