# DS-01 handoff (continuation 0 → 1)

Task prompt: `~/tumnis-coordinator/prompts/DS-01.md` (binding; read it first). Scott decision 20.
Branch: `wp/DS-01-impl` (pushed with this file). **No PR opened yet.** PR 2 (`wp/DS-01-impl-2`) and PR 3 (`wp/DS-01-impl-3`) not started.

## Commits on wp/DS-01-impl (oldest first)
- `6f2f57f` test(frontend): DS-01 spec tests (red): Vitest T-DS-01-01..07, Playwright T-DS-01-08..12, typed stubs
- `ed2452e` feat(frontend): tokens, self-hosted Inter, remembered theme (removes T-DS-01-01/02 markers)
- `2e207a6` feat(frontend): collapsible sidebar, header menus, phone drawer (removes T-DS-01-03..07 markers)
- `f929224` docs(adr): ADR-0012 visual design; ARCHITECTURE open question 2 closed; Part A row "Files and names (DS-01)"
- `b140a9f` test(frontend): Playwright markers removed (all 5 DS-01 e2e specs passed at both widths)
- `d6c44db` merge origin/main (fa5da78, #68). Only conflict: Part A table in docs/IMPLEMENTATION-PLAN-DETAILED.md; both rows kept (DS-01 and P2-13).
- this HANDOFF commit

## State
- All markers removed; no `test.fails`/`test.fail()` left from DS-01.
- Vitest: full suite 99/99 green before the merge (`npx vitest run --maxWorkers=4`; the default worker count times out under this VM's load, ~50 load average).
- Playwright (`make e2e` against a stack started with `docker compose -p ds01 -f deploy/compose.test.yaml up -d --wait --build`, port 8080, before the main merge): 41 passed, 5 skipped, and the only "failures" were the 8 DS-01 specs reporting "expected to fail, but passed" (fixed by b140a9f). Every locked spec stayed green, including T-P0-22-17 (shell keyboard + axe), T-P0-23-10 (no scroll at 1280x800), T-P0-23-11, T-P0-24-17, T-P0-26-10, J2.
- `make check` after the merge (d6c44db): backend, lint, format and typecheck green; Vitest had 8 failures at the default worker count. A `--maxWorkers=4` re-run had 2 failures (AgentsSection T-P1-04-19, CalendarSection T-P1-09-12), both of which passed in every earlier run, and a third run of a subset failed a different set of 7 tests. The failing set moves between runs (findBy/20 s timeouts at load average ~52), so this looks like machine load, not the DS-01 changes. Not yet shown clean on the merged tree: re-run when load is lower, or rely on CI's unit job, before opening the PR.
- The ds01 compose stack is being taken down with this handoff (`docker compose -p ds01 -f deploy/compose.test.yaml down -v`).

## Remaining steps
1. `make check` with SEMGREP_* under `$TMPDIR/DS-01-c1/semgrep/` (if Vitest times out, rerun `npm --prefix frontend run test -- --run --maxWorkers=4`).
2. `make e2e` once more on the merged tree (bring the stack up as above; `make down`/`docker compose -p ds01 ... down -v` after).
3. Screenshots: a ready helper spec is at `/tmp/claude-1002/DS-01-c0/zz-ds01-shots.spec.ts` (writes PNGs to `/tmp/claude-1002/DS-01-c0/shots/`). Copy it into `frontend/e2e/` untracked, run `make e2e`, delete it again, never commit it. Upload the PNGs as PR comment attachments or describe them.
4. Bundle size: before = **152.7 KB** gzipped initial JS (main at b7dcb15). After: run `npm --prefix frontend run build && npm --prefix frontend run bundle` and report. (vite output suggests the entry grew only by the shell code; fonts are CSS assets, not JS.)
5. Push (`/usr/bin/git push origin HEAD:wp/DS-01-impl`), open PR 1: `gh pr create --base main --head wp/DS-01-impl --title "[DS-01] impl: Mosaic-style tokens, theme toggle and app shell" --body-file <file>`, body ends with a blank line + `🤖 Generated with [Claude Code](https://claude.com/claude-code)`, then `gh pr comment <url> --body "@coderabbitai review"` and run the CodeRabbit loop (`~/tumnis-coordinator/pr-review-loop.md`). Never merge.
6. Then PR 2 (primitives: buttons, inputs, selects, cards, dropdowns, dialogs, badges, tables restyled on the tokens; replace the hard-coded emerald/amber/red status classes in HealthBadge/DeployStatus with `text-success`/`text-warning`/`text-danger` tokens) on `wp/DS-01-impl-2` started from impl; PR 3 (dashboard/project/review card styling + lazy Chart.js wrapper with a test that it is not in the initial bundle) on `wp/DS-01-impl-3`. Chart.js latest is 4.5.1 (check Context7 before pinning).

## Decisions and deviations (for the PR body)
- No Mosaic code, CSS or images in the repo. Its values were studied (read-only via `gh api`) and the look rebuilt; ADR-0012 credits it. Icons are hand-drawn simple SVGs in `components/common/icons.tsx`.
- Colours: Mosaic hues, but light-mode accent `#5d47de` (violet-700, 6.1:1 on white) and muted text `#5b6472` (5.4:1 on the gray-100 page) instead of Mosaic's violet-500/gray-500, which fail AA. Dark: accent `#9c8cff`, surface `#1f2937`, bg `#111827`.
- Theme: `data-theme` on `<html>`; `@custom-variant dark` redefined so existing `dark:` classes follow a manual choice; `public/theme-init.js` (same-origin file, CSP forbids inline script) applies the stored choice before first paint.
- Inter: `@fontsource-variable/inter@5.3.0` (new devDependency, pinned exactly; shared-file edit of `frontend/package.json` + lock). Subset-only imports are not available for variable fonts (Fontsource docs), so `wght.css` is imported and `unicode-range` limits downloads; only the Latin woff2 is precached (`vite.config.ts` globPatterns edit).
- Phone: bottom bar kept (thumb reach; locked tests need a visible "Primary" nav on phones); header menu button opens a modal drawer (`dialog "Menu"`, nav "Menu", focus trap, Escape, inert page). BottomBar moved before `main` in the DOM so the keyboard meets navigation first on both widths.
- Header search sets `uiStore.searchOpen` (the P0-25 palette in PR #65 reads it). Until #65 merges, the button opens nothing visible — note for Scott/coordinator.
- Header review link is named `Review: N waiting` (not "N to review", which the locked T-P0-23-06 uses for the dashboard badge). Dashboard badge kept.
- Help: a disclosure panel (`region "Keyboard shortcuts"`) listing Search (Ctrl K/⌘K), Quick add (/), Esc. Account menu: WAI-ARIA menu button (Settings, Theme radios Light/Dark/System, Sign out via POST /v1/auth/logout). The account email loads only when the menu opens.
- Dashboard height now subtracts `--tg-header-h` (one-line edit in DashboardPage.tsx) so T-P0-23-10 stays green.
- A small focus-trap helper lives inside `NavDrawer.tsx` rather than `lib/focusTrap.ts`, to avoid an add/add conflict with PR #65, which adds that file; dedupe after #65 merges.
- `uiStore` gained `theme`, `sidebarCollapsed`, `navOpen` and their events (shared file; T-P0-22-18 uses toMatchObject, still green).

## Context7 / docs used
- Tailwind v4 dark mode with a data attribute + custom variants (`/websites/tailwindcss`, tailwindcss.com/docs/dark-mode).
- Fontsource variable Inter, subsets note (`/websites/fontsource`).
- TanStack Router Link `activeProps`/`activeOptions`, `data-status="active"` and `aria-current` (`/tanstack/router`).

## Scott items
- None blocking. FYI: header search depends on PR #65 (P0-25) for the palette itself.

## Gotchas
- Plain `git` is rewritten by rtk; use `/usr/bin/git`. The worktree guard refuses compound shell commands with `cd`/globs/loops; keep commands plain.
- Sandboxed commands cannot reach 127.0.0.1; Playwright only runs bare via `make e2e`.
- `.claude/` and `.mcp.json` are untracked and not ours; never commit them.
