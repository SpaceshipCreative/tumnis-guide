# HANDOFF: P1-17 (Knowledge items, editor, search and passages), continuation c2

Written on "HANDOFF NOW" from the context watcher. **This file lives on `wp/P1-17-impl-2`,
not on `wp/P1-17`**, so PR #122's diff stays clean (c2 removed the old HANDOFF.md from #122
in af6ed60). Scratch folder used: `$TMPDIR/P1-17-c2/` (= /tmp/claude-1002/P1-17-c2/).

Two branches now:

| Branch | What | Push with |
| --- | --- | --- |
| `wp/P1-17` | PR #122 (backend), head **74a85e6** | `/usr/bin/git push origin HEAD:wp/P1-17` |
| `wp/P1-17-impl-2` | PR 2 (editor), NOT opened yet: 74a85e6 + the red spec commit + this file | `/usr/bin/git push origin HEAD:wp/P1-17-impl-2` |

Setup for c3: work on #122 and on PR 2 in SEPARATE throwaway worktrees (or finish #122 first),
because any PR 2 commit on HEAD would ride along on a later `HEAD:wp/P1-17` push.
- For #122: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P1-17`.
- For PR 2: same, then `/usr/bin/git merge origin/wp/P1-17-impl-2`.
- Setup: `cd backend && uv sync --frozen --all-extras`; `npm ci --prefix frontend` (PR 2 adds Tiptap).

## PR #122 state (https://github.com/SpaceshipCreative/tumnis-guide/pull/122)

Commits by c2 on top of c1's 02ef08f:

| SHA | What |
| --- | --- |
| 755ed17 | merge origin/main (eb363ad, the Trivy fix) |
| b62efd1 | `fix(core)`: CodeRabbit thread 4151348305 (405 Allow), FixedAllow marker: **superseded** |
| 22d6a64 | `fix(core)`: routes `declines(path_params)`; the 405 handler always aggregates Allow across routes but leaves out declining routes. FixedAllow removed. Fixed the contract fuzz (PUT /knowledge/documents/text answered `Allow: DELETE, GET, PATCH, POST`). Unit test `backend/tumnis/core/tests/unit/test_method_not_allowed.py` |
| af6ed60 | `chore`: removed HANDOFF.md from #122 |
| 74a85e6 | merge origin/main (P2-05 #121, 8b24efc): `backend/tests/audit_cases.py` keeps both sides (document.trust_changed + approval.granted/denied); `make gen` left generated files unchanged |

- **CI on 74a85e6: all green** (contract, daemon, e2e, integration 9m59s, lint, performance,
  red-proof, security, skills, spec-guard, traceability, unit, version-skew, GitGuardian).
  `preview` pending (no homelab runner, expected).
- **CodeRabbit:** all 6 threads resolved (5 from round 1 fixed in 2e34964, replied by c2;
  4151348305 fixed in b62efd1 then 22d6a64, replied twice). c2 posted `@coderabbitai review`
  ONCE (comment 5923892699). At handoff a CodeRabbit review on the latest push was
  "in progress": read it (GraphQL reviewThreads + review bodies), fix or reply, resolve.
  Don't request another full review unless needed (quota).
- PR body updated (from `$TMPDIR/P1-17-c2/pr-body.md`): T-10 green, deviation 8 (declines
  hook), shared files (core/errors.py hook, workflows.py), CodeRabbit fixes, RFC 9110 15.5.6.
- **Next for #122:** handle the pending CodeRabbit review; when CI is green and 0 open
  threads, SendMessage to "main": `#122 MERGE-READY at <sha>`. The performance job passed
  on 74a85e6 (the board TBT issue is owned by fix/board-tbt; never change the budget or the
  board page). Merge origin/main again whenever the coordinator says another PR merged
  (regenerate with `make gen`, never hand-merge generated files).

## PR 2 (editor) state: red spec commit on wp/P1-17-impl-2

Committed as `test(frontend): P1-17 spec tests (red)`: 53 strict expected failures
(`test.fails`), all confirmed red with
`cd frontend && npx vitest run src/editor src/components/project/PacketPreview.test.tsx src/components/project/rail/KnowledgeSection.test.tsx tests/`
(typecheck, eslint and prettier clean). Spec tests:

| ID | File |
| --- | --- |
| T-P1-17-01 | `frontend/src/editor/roundtrip.test.ts` (15 fixtures x 3 tests) |
| T-P1-17-02 | `frontend/src/editor/useNoteSave.test.tsx` |
| T-P1-17-03 | `frontend/src/editor/links.test.ts` |
| T-P1-17-04 | `frontend/src/editor/makeTask.test.ts` |
| T-P1-17-05 | `frontend/src/editor/markdown.test.ts` |
| T-P1-17-16 | `frontend/src/components/project/PacketPreview.test.tsx` |
| T-P1-17-17 | `frontend/src/components/project/rail/KnowledgeSection.test.tsx` (laptop + phone sheet) |
| T-P1-17-18 | `frontend/tests/bundle.test.ts` (vite build in memory, write:false, VITEST unset during build so route splitting is on; ~30 s) |

Also in that commit: fixtures `frontend/src/test/fixtures/roundtrip/*` (+ `.canonical.md`
twins, `table-unsupported.skip-roundtrip`), `frontend/.prettierignore` (fixtures are
byte-exact), `frontend/src/test/msw/knowledge.ts` (KnowledgeFake), `makeDocument` in
`frontend/src/test/factories.ts`, stubs for every module, `vitest.config.ts` include
`tests/**/*.test.ts`, `tsconfig.json` include `tests`, and deps pinned exactly in
`frontend/package.json`: `@tiptap/{core,react,pm,starter-kit,extension-list,extension-link,markdown,suggestion}@3.31.4`
and `marked@17.0.6` (the version @tiptap/markdown resolves; used for canEditSafely).

**Implementation drafts (NOT committed, NOT yet run)** are in `$TMPDIR/P1-17-c2/impl/src/editor/`:
`markdown.ts`, `links.ts`, `slash.ts`, `extensions.ts`, `makeTask.ts`, `useNoteSave.ts`,
`Editor.tsx`. Copy them over the stubs, then make each test green and remove its marker one
at a time. If $TMPDIR was wiped, rebuild from the design below.

Design (decided by c2, from experiments in jsdom with Tiptap 3.31.4):
- `getMarkdown()` output is already canonical except it has no trailing newline:
  `canonicalBody` = strip trailing `\n`s, add one (empty stays empty). `* x` becomes `- x`,
  `__a__`/`_b_` become `**a**`/`*b*`, `<https://x>` becomes `[https://x](https://x)` (so the
  links fixture avoids angle autolinks), a table collapses into one paragraph (so canEditSafely
  is false). Mentions `[T](tumnis://task/<id>)` round-trip with `Link.configure({protocols:['tumnis']})`.
- `canEditSafely`: compare `new Lexer({gfm:true}).lex()` token trees of body vs round-tripped
  body, dropping `raw`, `space` tokens, and `text` where `tokens` exist.
- Mentions use `@tiptap/suggestion` directly in a small `MentionLinks` extension (no Mention
  node, so no mention syntax can parse into a node the save path never writes); the pick
  inserts linked text then `unsetMark("link")` (Link is inclusive when autolink is on).
  `SuggestHandlers.onOpen(SuggestOpen|null)` reports the menu; the Editor renders it as a
  listbox under the editor; `onKeyDown` handles arrows/Enter/Escape.
- Link safety: `isAllowedUri` = defaultValidate AND `^(https?://|mailto:|tumnis://(task|doc)/)`.
  Tiptap's `renderHTML` blanks a disallowed href; markdown parse keeps the href text, so the
  round trip never loses it. Cite Tiptap Link docs (isAllowedUri) and the Tiptap 2025-06-25
  link-popover XSS incident note.
- jsdom cannot type into ProseMirror (DOM typing does not reach the view; elementFromPoint and
  getClientRects missing). Tests drive the editor by transactions (`onReady(editor)` prop).
- `useNoteSave`: debounce `SAVE_DEBOUNCE_MS` (400), PATCH `/v1/knowledge/documents/{id}`
  `{body_md, version}` through `apiWrite`; the baseline is `roundTrip(loaded)`, so opening
  never writes; one save in flight; 409 shows a conflict.
- Editor: `editorMode(src)` decides rich vs source (textarea + notice) once per loaded version.
- Remaining to build: `KnowledgeSection.tsx` (summary "N items · X MB of Y GB" from
  `GET /v1/knowledge/quota?project_id=`; list from `GET /v1/knowledge/documents?project_id=`;
  Add text (Title/Text fields, Add), Upload (`<input type=file>` labelled "Upload a file",
  multipart via `apiFetch` with Idempotency-Key), Add link (field "Link"), Pin/Unpin
  `PATCH {pinned, version}`, trash `DELETE` with an "Undo" button that POSTs `/restore`;
  text entries open the lazy Editor), wire it into `rail/RailSections.tsx`, add a quota
  handler (and an empty documents list) to `ProjectFake` (test/msw/project.ts) so the locked
  rail tests still have every request handled (MSW `onUnhandledFrame: "error"`).
  `PacketPreview.tsx` (`region` "Packet preview", `agentsGetTaskPacketOptions` with
  `kind: "enrich"`, brief then passages cited "Title, page N"), shown in the task drawer
  behind a "Preview packet" button. `LazyEditor` = `React.lazy(() => import("../editor/Editor"))`
  so Tiptap stays out of the initial graph (T-18).
- BriefSection and its locked test stay UNTOUCHED (Scott item 3 below). Coordinator: if PR 2 is
  otherwise ready before Scott answers, open it with option (b) (brief stays a textarea) and
  list it as a deviation pending Scott.

PR 2: title `[P1-17] impl: knowledge editor`, branch `wp/P1-17-impl-2`. Base: main if #122
has merged by then, otherwise `wp/P1-17`. Request CodeRabbit once when it opens.

## Decisions and deviations (PR 1 list in its body; PR 2 so far)

PR 2:
1. T-P1-17-02 asserts `PATCH` (the backend route, R-36 / PR 1), not the plan's "PUT".
2. Keystrokes in tests are editor transactions (jsdom has no layout for ProseMirror).
3. Mentions through `@tiptap/suggestion` in a `MentionLinks` extension, not `Mention.configure`
   (no Mention node or mention Markdown syntax registered). `@tiptap/extension-mention` not added.
4. `canEditSafely` compares marked token trees (marked is already @tiptap/markdown's parser),
   not mdast (no remark dependency added).
5. Round-trip harness reads fixtures by filesystem path (`fileURLToPath`), since under jsdom
   `URL` is jsdom's and node:fs refuses it; `.skip-roundtrip` siblings are filtered out.
6. `vitest.config.ts` and `tsconfig.json` include `tests/` (the plan's path for T-18).
7. `editorMode(src)` added to markdown.ts so T-05 checks the source mode without rendering.
8. The brief stays in its textarea pending Scott (item 3).

## Scott items

1. **A1.5 step 4 vs T-P2-02-14 (both locked).** A1.5 calls `GET /v1/tasks/{id}/packet` with no
   `kind` and expects `enrich`; T-P2-02-14 expects the default `task`. #122 adds `kind=enrich`
   and keeps the default `task`. Options: (a) A1.5 passes `kind=enrich`, (b) a session defaults
   to enrich. A1.5's markers stay on.
2. A1.5 integration steps 1-4 and Playwright A1.5 also need real Docling in CI and an
   `Acme site` seed project (Scott items on #102; no seed data from us).
3. **Brief into the editor vs locked `rail/BriefSection.test.tsx`** (`toHaveValue` on textbox
   "Brief", `user.type`): impossible on a ProseMirror contenteditable. Options sent to the
   coordinator (logged as a Scott item): (a) spec-change PR adapts that test, (b) keep the
   textarea for now (deviation), (c) textarea stays as source view, editor in a separate screen.

## Docs consulted (cite in PR bodies)

- #122: PostgreSQL 18 text search; SQLAlchemy 2.0; Hypothesis; Starlette routing (and
  `Route.handle`'s own 405 with `Allow`); RFC 9110 15.5.6 (verified on rfc-editor.org:
  "The server MUST generate an Allow header field in a 405 response...").
- PR 2: Tiptap docs via Context7 `/ueberdosis/tiptap-docs`: Markdown extension
  (install, `Markdown.configure`, `contentType: 'markdown'`, `getMarkdown`), v3 list package
  (`@tiptap/extension-list` TaskList/TaskItem), Mention `suggestions` / Suggestion utility
  render hooks, Link `protocols` / `isAllowedUri` and the 2025-06-25 link popover XSS incident;
  installed source read for Link (`renderHTML` blanks disallowed hrefs, `parseMarkdown`) and
  Suggestion (no focus requirement). marked via Context7 `/markedjs/marked` (Lexer, token fields).

## Verify commands

- `bash $TMPDIR/P1-17-c2/check.sh` (make check with semgrep files under $TMPDIR; edit its `cd` line)
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/core tumnis/modules/knowledge`
- `make test` (unit + contract, bare) passed 2088 on 22d6a64.
- `cd backend && uv run python $TMPDIR/P1-17-c2/probe.py` (405 + `Allow: POST` on the literal paths, incl. PUT/TRACE)
- `$TMPDIR/P1-17-c2/watch_ci.sh <PR>` for a Monitor on CI.
