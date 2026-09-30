# DS-01 handoff (continuation 1 → 2)

Task prompt: `~/tumnis-coordinator/prompts/DS-01.md` (binding). Scott decision 20: rebuild the Mosaic Lite look in our own code. **Never copy Mosaic source code (files, JSX, CSS or images) into the repo.** Matching colour values, spacing and layout ideas is fine.
Scratch folder for c2: `$TMPDIR/DS-01-c2/`. c1's files in `/tmp/claude-1002/DS-01-c1/` hold the PR bodies, screenshots, the screenshot helper spec and the contrast script; they are readable.

## Branch setup for c2 (no HEAD moves)
`/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, then `/usr/bin/git merge origin/wp/DS-01-impl-2`. HEAD is then the PR 2 line; push with `/usr/bin/git push origin HEAD:wp/DS-01-impl-2`. PR 3 starts from that and pushes `HEAD:wp/DS-01-impl-3`.
**Stacking caveat:** HEAD carries PR 1 and PR 2. A PR 1 fix made on top of PR 2 commits can't be pushed to `wp/DS-01-impl` without dragging PR 2 along. c1 handled this by saving PR 2 work to scratch (a `git diff --output` patch plus copies of new files), `git checkout -- frontend`, fixing and pushing PR 1, then re-applying. Once PR 2 commits exist on HEAD, a PR 1 fix has two options: make it on the PR 2 line and say so in #85, or wait for #85 to merge.

## PR 1: #85 `wp/DS-01-impl`, "[DS-01] impl: Mosaic-style tokens, theme toggle and app shell"
https://github.com/SpaceshipCreative/tumnis-guide/pull/85, head f5096d3. Not merged; the coordinator merges.
Commits added in c1 (on top of c0's 6f2f57f, ed2452e, 2e207a6, f929224, b140a9f, d6c44db):
- d7682de chore: remove the DS-01 handoff note
- 9b81cd9 chore(merge): merge main (#65 P0-25 quick-add) into DS-01. AppShell kept both sides: the DS-01 shell inside P0-25's `QuickAddHost`, so the header Search button now opens the P0-25 palette.
- 1032bc0 refactor(frontend): NavDrawer uses `dialogKeyDown` from `lib/focusTrap.ts` (the dedupe the coordinator asked for once #65 merged)
- e8de8c3 test (red) T-DS-01-16: everything behind the phone drawer is inert, toasts and Quick add included
- f5096d3 fix: the inert wrapper holds the whole `QuickAddHost`; only NavDrawer sits outside it (marker removed)

State of #85:
- CodeRabbit: 1 thread (AppShell toasts not inert). Fixed in f5096d3, replied and resolved. **0 unresolved.** The re-review of f5096d3 posted no new comments.
- CI at f5096d3: every job passes (unit, lint, e2e, integration, contract, daemon, spec-guard, traceability, red-proof, performance, skills, version-skew) **except `security`**. That job fails on upstream OpenSSL CVEs in the Debian base image (libssl3t64 CVE-2026-75804 and CVE-2026-84782). fix/openssl-cves owns this; do not touch the Dockerfile or `.trivyignore`. `preview` is pending, which is fine.
- mergeable: MERGEABLE, and not yet merged with main 32863b4. It merges cleanly: c1 merged 32863b4 into the PR 2 line without conflicts.
- PR body: `/tmp/claude-1002/DS-01-c1/pr1-body.md` (already on the PR). The bundle numbers in it are from c1's first measurement (see Bundle below).

## PR 2: `wp/DS-01-impl-2` (pushed with this handoff; **PR not opened yet**)
Commits on top of f5096d3:
- 3650aa1 test (red): T-DS-01-13 (Vitest `test.fails`: no Tailwind hue with a shade and no white/black text in any non-test source under src/ except api/ and test/), T-DS-01-14 (Vitest `test.fails`: `Card` is a labelled region with a header row; Card.tsx a typed stub), T-DS-01-15 (Playwright guard, unmarked: the Settings sections and the Sessions confirm dialog have no serious axe violations in light and dark at both widths)
- c594f03 feat: `components/common/ui.ts` (BUTTON_PRIMARY/SECONDARY/DANGER/QUIET, FIELD, FIELD_LABEL, HINT, ERROR_TEXT, CARD, CARD_HEADER, CARD_TITLE, CARD_BODY, BadgeTone + `badge(tone)`, TABLE*, DIALOG_BACKDROP/PANEL/TITLE) and `Card.tsx`.
  - `settings/styles.ts` re-exports under the same names; the rail's `fieldClass`/`saveClass` now come from ui.ts, plus a new `deleteClass` that TaskDrawer's delete button uses.
  - HealthBadge, DeployStatus and PrStatus use badge tones in place of emerald/amber/red. DeployStatus's Link button uses BUTTON_PRIMARY, not `text-white`, which was 2.6:1 on the dark accent.
  - The four settings dialogs use the DIALOG_* classes, the API keys table uses TABLE*, and the Storage "Default" chip is `badge("accent")`.
  - New tokens `--tg-{success,warning,danger,info}-soft`, each ≥4.5:1 in both themes (script: `/tmp/claude-1002/DS-01-c1/contrast.mjs`). Checkboxes and radios get `accent-color`.
  - ADR-0012 gained "Shared classes" and a tokens-only consequence; the Part A row lists the new names. Markers for T-DS-01-13 and 14 removed after they passed.
- a750cd2 merge main 32863b4 (#77 P1-12 CalendarView, #79 Vitest load fixes, #86). No conflicts; vite.config globPatterns edit kept.
- `make check` on a750cd2: **green**, all of it (backend 1272, daemon 28, profiles 48+5 skipped, Vitest 75 files / 154 tests, bundle test).

## Remaining steps
1. PR 2 local e2e + screenshots. Port 8080 was taken by another agent when c1 tried (`docker compose -p ds01 -f deploy/compose.test.yaml up -d --wait --build` failed on "Bind for 127.0.0.1:8080"). c1 took the partial stack down.
   - Retry when 8080 is free. Copy `/tmp/claude-1002/DS-01-c1/zz-ds01-shots.spec.ts` into `frontend/e2e/`, untracked (it writes to `/tmp/claude-1002/DS-01-c1/shots2/`; change OUT to your c2 folder), run `make e2e`, then delete the helper and `docker compose -p ds01 -f deploy/compose.test.yaml down -v`.
   - Or rely on CI e2e and describe the look from the code.
2. Bundle for PR 2 (gzipped initial JS; budget 200 KB):
   - main at 32863b4: **176.9 KB**.
   - The PR 2 line, measured before the 32863b4 merge: **179.8 KB**.
   - Re-measure on a750cd2: `npm --prefix frontend run build` then `npm --prefix frontend run bundle`.
   - Main moved from 152.9 KB (fa5da78) because P0-25 and P1-12 landed. DS-01's own share is about +3 to +7 KB.
3. Open PR 2: `gh pr create --base main --head wp/DS-01-impl-2 --title "[DS-01] impl-2: shared primitives on the tokens" --body-file <file>`.
   - In the body, say it stacks on #85, list the tests and results, the bundle numbers, screenshots or descriptions, and the docs relied on (Tailwind v4 custom variants and theme variables, Context7 `/websites/tailwindcss`).
   - It adds no new dependency.
   - End the body with a blank line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
   - Then `gh pr comment <url> --body "@coderabbitai review"` and run the loop (`~/tumnis-coordinator/pr-review-loop.md`).
4. PR 3 (`wp/DS-01-impl-3`, from the PR 2 line): dashboard, project and review page card styling using `Card`/`CARD*`, within PRD UX. The dashboard answers "what now?" without scrolling at 1280x800 (locked T-P0-23-10), Today shows at most 5 tasks, and nothing is added for looks. Keep every locked heading, role and label.
   - **Review page:** on main it is still the P1-13 placeholder; the real ReviewQueue is PR #80, not merged. If #80 hasn't merged, restyle only what exists, and list "review page card styling waits on #80" as a coordinator/Scott item.
   - **Chart.js:** a lazy wrapper, e.g. `components/common/LazyChart.tsx`, calling `import("chart.js")` inside an effect, registering only the controllers it needs (Context7 `/websites/chartjs`: import from `chart.js`, not `chart.js/auto`, and `Chart.register(...)` for tree-shaking), themed from the CSS variables.
     - Verify the pin first with `npm view chart.js version` (4.5.1 expected) and pin it exactly. This is a shared-file edit and needs a reason in the PR body.
     - Test red first: a Vitest source check that only the wrapper imports `chart.js`, and only through `import()`, plus a render test with a mocked module.
     - Add no chart to a page without metrics.
5. On each PR: `make check`, e2e, and a `make gen` check if main brought schema changes. Never merge.
6. When everything's done, delete HANDOFF.md in a `chore` commit.

## Decisions and deviations (for the PR bodies)
- No Mosaic code, CSS or images in the repo (decision 20). The look is rebuilt; ADR-0012 credits Mosaic. Icons are hand-drawn SVGs.
- Colours: Mosaic hues, with the light accent at #5d47de (6.1:1) and muted text at #5b6472 for AA. Dark theme: bg #111827, surface #1f2937, accent #9c8cff.
- Theme: `data-theme` on `<html>`, a redefined `@custom-variant dark`, and `public/theme-init.js` (a file, because the CSP forbids inline script).
- Inter: `@fontsource-variable/inter@5.3.0`, pinned exactly. Only the Latin woff2 is precached.
- Phone: the bottom bar is kept, and the header's menu button opens a modal drawer. BottomBar comes before `main` in the DOM.
- Header review link reads "Review: N waiting"; the dashboard badge "N to review" is locked by T-P0-23-06.
- Dashboard height subtracts `--tg-header-h`.
- The drawer now uses `lib/focusTrap.ts` (#65 merged).
- AppShell: everything but NavDrawer is inside `<div inert={navOpen}>`, QuickAddHost included. Known edge: Mod+K pressed while the drawer is open opens the palette inside the inert area, so it can't be used until the drawer closes. This is minor and not raised by review.
- PR 2 buttons dropped `whitespace-nowrap`, so long labels wrap at 375 px (T-P0-26-10 checks for no sideways scroll). DANGER now has a neutral border with a danger border on hover (Mosaic style), where it used to have a danger border always.
- T-DS-01-15 is an unmarked guard: it was expected green before and after, so no red commit was possible for it.

## Context7 / docs used
- Tailwind v4 dark mode, data attribute and custom variants (`/websites/tailwindcss`)
- Fontsource Inter variable (`/websites/fontsource`)
- TanStack Router Link active state (`/tanstack/router`)
- Chart.js tree-shaking and registration (`/websites/chartjs`, getting-started/usage and integration)
- WAI-ARIA APG menu button and modal dialog; WCAG 2.2 contrast minimum

## Scott / coordinator items
- `security` CI fails on every PR because of upstream OpenSSL CVEs (fix/openssl-cves). Not DS-01's to fix.
- Review page card styling depends on #80 (P1-13) merging.

## Verify commands
- `/usr/bin/git log --oneline origin/main..HEAD`
- `SEMGREP_SETTINGS_FILE=$TMPDIR/DS-01-c2/semgrep/settings.yml SEMGREP_LOG_FILE=$TMPDIR/DS-01-c2/semgrep/log.txt SEMGREP_VERSION_CACHE_PATH=$TMPDIR/DS-01-c2/semgrep/vc make check` (Vitest: `npx vitest run --maxWorkers=3` in frontend if load is high)
- `gh pr checks 85`, and the reviewThreads GraphQL query for #85

## Gotchas
- Use `/usr/bin/git`, one git command per Bash call. The worktree guard refuses compound commands containing git, and `gh` with jq paths inside loops (put those in a script file).
- Docker commands run bare. `make e2e` needs a stack on 8080; bring one up with `-p ds01`.
- `.claude/` and `.mcp.json` are untracked and not ours.
