# HANDOFF: fix/board-tbt (board view total-blocking-time)

Handed off on "HANDOFF NOW" from the context watcher. No PR opened yet; no CI run on the
change yet; no CodeRabbit review requested yet.

## State

- Branch `fix/board-tbt`, based on main `eb363ad` (includes #123, the libpcre2 fix, so the
  `security` job should pass).
- Commits:
  - `35c534d` perf(frontend): cut board view blocking time (the whole change)
  - this file (chore: perf-board handoff)
- `make check` passed on `35c534d` (frontend typecheck, lint, 93 Vitest files / 201 tests,
  backend unit). Full frontend Vitest suite passed too.
- Bundle: `scripts/ci/check_bundle.mjs` on a local build: 185.3 KB initial JS gzipped
  (was 184.6 KB; budget 200 KB). `lib/validate.ts` lands in an initial `queries` chunk.
- Nothing in lighthouserc*, ci.yml, required-checks.txt, test_ci_config.py,
  perf/baseline.json or any existing test was touched.

## Where the time went (evidence)

CI LHRs (`gh run download <run> --name performance`, needs `allowed_domains:
["*.blob.core.windows.net"]` in the sandbox): main 5d488a0 run 36799478425 board TBT
216/162/212 ms; PR #120 run 36806911032 board TBT 218/159/154 ms. Every board run has the
same three post-FCP long tasks: `react-query.gen` 104-134 ms (zod parse of the 278 KB board
response; the chunk holds zod + generated client), `fetch` chunk 114-167 ms (query-core +
react: the board's query result rendered synchronously through useSyncExternalStore), then
`index` 57-117 ms (react-dom: commit plus dnd-kit's registration re-render). DOM 3,045
nodes; JSON on the wire: two task pages (152 KB + 118 KB) and the board (278 KB); k6's
150 quick-adds land in project 01's backlog before Lighthouse runs (about 350 tasks).
Main-thread breakdown also had 218 ms Style & Layout and 92 ms Rendering.

Local harness (all in `$TMPDIR/perf-board-c0/`, i.e. /tmp/claude-1002/perf-board-c0/):
`mock.mjs` (serves a dist + synthetic load-set API: 350 tasks, 323 cards), `trace.mjs` +
`tasks.py` (Chrome trace + CPU profile at 2x CPU, 375 px, per-task attribution),
`run-trace.sh <dist> <tag>`, `multi.sh <dist> <tag> <n>`, `run-lh.sh <dist> <tag>`
(local LHCI 0.15.1 with the repo's lighthouserc.json; noisy on this loaded VM),
`build.sh <tag>` (minified + unminified builds). The mock must run in the same Bash call
as the browser (sandboxed commands do not share localhost).

Local traces, tasks over 15 ms after the data lands (2x CPU):
- before (dist-before-nm): 44 ms (ProjectPage: groupOf -> Intl date formatting), 94 ms
  (sync board render: BoardCard/useSortable + DOM creation), 102 ms (layout 42 + paint 47),
  90 ms (dnd-kit registration re-render: DndContext reducer copies its droppable map per
  card, O(n^2), plus every useSortable), 39 ms (style). Local TBT ~136.
- after (dist-v3-nm, = commit 35c534d): 23-28 ms, 18-20 ms (layout), 25-26 ms (layout/paint),
  21-22 ms (registration re-render). Local TBT ~0.

## What changed (commit 35c534d)

1. `frontend/src/lib/validate.ts` (new): `parsePageInSlices` / `parseBoardInSlices` run
   the generated zod schemas over list items 20 at a time, yielding between slices
   (`scheduler.yield()` else MessageChannel); same issues (paths) in one ZodError.
   `components/project/queries.ts` passes them as `responseValidator` (the hey-api client
   ignores the validator's return value; `...options` spreads after the generated one) for
   both task pages and the board; `boardQuery` gets its own queryFn under the generated key.
2. `BoardView.tsx`: the first render of a loaded board happens in `startTransition`
   (time-sliced); "Loading the board…" until then. Later updates unchanged.
3. `BoardCard.tsx`: card contents in a memoised `CardBody`; dnd-kit context re-renders only
   re-run the sortable shell. Cards get `content-visibility: auto` with
   `contain-intrinsic-size: auto 5.5rem`, switched to visible while the Move menu is open
   (`has-[[role=menu]]`) because content-visibility clips overflow (the menu hangs below).
4. `ProjectPage.tsx`: `items` and `today` in useMemo. `grouping.ts`: `doneRecently()`
   decides "done recently" from elapsed time unless within 4 to 10 days (only then formats
   dates); `localDaysBetween` caches today's local day. Property test
   `doneRecently.test.ts` (fast-check, 2000 runs, far zones + DST) proves the same answer;
   mutation-checked (a 6.5-day bound fails it).
5. `routes/projects.$projectId.tsx`: when the URL says `view=board` and the board is not
   cached, the loader starts `queryClient.query(boardQuery)` without awaiting (paused
   offline like the board's own query), so the board read runs beside the page's reads.
6. Tests: `src/lib/validate.test.ts` (4 tests: same verdict and issues as the generated
   validator for pages and the board, odd shapes, yields counted with a stubbed
   `scheduler.yield`), `src/components/project/doneRecently.test.ts` (2 tests). Tags
   `[P0-29][PERF-2]`.

## Docs consulted (cite in the PR body)

- React `useDeferredValue` / transitions: https://react.dev/reference/react/useDeferredValue
  (Context7 /websites/react_dev_reference) — background renders are interruptible; chose
  startTransition on first reveal instead so later updates stay synchronous.
- TanStack Router loaders and search params (Context7 /tanstack/router, data-loading guide);
  `loaderDeps` deliberately not used (a dep change would re-run the loader on every view
  switch); the loader reads `location.search` only as a prefetch hint.
- TanStack Query 5.104: `prefetchQuery` is deprecated in the pinned version (lint rule);
  used `queryClient.query(options).catch(...)`.
- hey-api client (pinned generated code, `frontend/src/api/client/client.gen.ts` lines
  186-194 and `core/types.gen.ts` `responseValidator`): the validator's return is ignored.
  Context7 /websites/heyapi_dev documents the validator plugin but not per-call override, so
  the pinned generated code is the authority.
- Zod 4 (Context7 /colinhacks/zod v4.3.6): array parse prefixes element issues with the
  index; `safeParse`/`ZodError(issues)`.
- CSS `content-visibility` / `contain-intrinsic-size`: W3C CSS Containment Module Level 2
  (https://www.w3.org/TR/css-contain-2/) — `auto` applies layout, style and paint
  containment (hence the menu exception) and skips off-screen contents. Confirm and cite
  before opening the PR (not yet fetched).

## Exact next steps

1. Push: `/usr/bin/git push origin HEAD:fix/board-tbt` (done with this handoff commit if
   the push succeeded; check `gh api repos/SpaceshipCreative/tumnis-guide/branches/fix/board-tbt`).
2. Open the PR: `gh pr create --base main --head fix/board-tbt --title "perf(frontend): cut
   board view blocking time" --body-file <file>` (body: where the time went, what changed,
   before/after board TBT per run, docs cited; end with a blank line and
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`). Do NOT post
   `@coderabbitai review` until the CI numbers are final (one review; quota is tight).
3. Get at least 3 `performance` job samples: `gh pr checks <N>`, find the run id, then
   `gh run rerun <run-id> --job <performance-job-id>` twice more. For each:
   `gh run download <run-id> --name performance --dir $TMPDIR/perf-board-c0/ci-<n>`
   (sandbox `allowed_domains: ["*.blob.core.windows.net"]`) and
   `python3 $TMPDIR/perf-board-c0/lhr.py $TMPDIR/perf-board-c0/ci-<n>` for per-run board TBT,
   LCP and FCP. Report all three runs per sample next to the before numbers
   (216/162/212, 218/159/154; failing medians 226, 226, 217, 214-257). Target: board TBT
   reliably under ~150 ms. LCP budget 2500 (before 1.8-2.2 s): watch it.
4. Then post `@coderabbitai review` once, work the review loop
   (~/tumnis-coordinator/pr-review-loop.md), and when CI is green with no open threads send
   "#<PR> MERGE-READY at <sha>" to main with SendMessage. Never merge.
5. If the CI numbers do not show real headroom, the remaining main-thread items are: the
   dnd-kit registration re-render (O(n^2) reducer copies; ~22 ms local), the commit of the
   board (~20 ms local), and the startup module evaluation (pre-FCP). Do not change any
   budget or lighthouserc setting; message main with numbers instead.

## Decisions and deviations

- The board's first render is deferred one transition; e2e tests wait with `findBy`/
  auto-waiting locators, and the full Vitest suite passes.
- `content-visibility: auto` on cards: Playwright treats auto-skipped content as visible
  (`checkVisibility()` without `contentVisibilityAuto`), dnd-kit reads card rects (forced
  layout works), and axe e2e only covers the shell pages. Watch the e2e board specs in CI.

## Scott items

- Not for this PR: in every LHR `transferSize` equals the resource size (index.js 319 KB on
  the wire), so the api serves the frontend bundle uncompressed. Compression would help LCP
  and FCP; it is a deploy/backend change someone should own.
