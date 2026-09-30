# ADR-0012: Visual design: a Mosaic-style look rebuilt on our Tailwind tokens, no UI kit
Status: Accepted (2026-09-30) | Supersedes: none (closes ARCHITECTURE open question 2; refines the styling note in ADR-0004)

## Context
ARCHITECTURE open question 2 ("Components and styling") left the UI kit open, with Tailwind CSS plus accessible headless primitives as the default. P0-22 built that default: Tailwind CSS v4, design tokens as CSS variables in `frontend/src/styles.css`, dark mode through `prefers-color-scheme`. The screens work but look plain. Scott wants the look of Cruip's Mosaic Lite dashboard template (scott-decisions item 20): a collapsible grouped sidebar, a header with search, notifications, help and a profile menu, a violet accent on a gray scale, Inter, and rounded bordered cards.

Mosaic Lite is GPL-3 with a term against redistributing the template, and this repository is public with no licence of its own. Copying its files, JSX, CSS or images would redistribute it.

The PRD's UX principles still bind every screen: the dashboard answers "what now?" without scrolling on a 13-inch laptop, Today shows at most 5 tasks, quiet by default, plain words, keyboard first, phone parity. The app runs on a private network, so nothing may load from a CDN or Google Fonts, and the CSP (P0-16) allows same-origin scripts, styles and fonts only.

## Options considered
- **Copy Mosaic Lite's components and CSS.** Fastest route to the look. Deal-breaker: GPL-3 with a no-redistribution term in a public repository.
- **Adopt a component kit (shadcn/ui, Radix Themes, Headless UI).** Accessible parts and a ready theme. Deal-breaker: a second styling system beside our tokens, more initial JavaScript against the 200 KB budget (PERF-2), and still not the look Scott asked for.
- **Rebuild the look ourselves on our Tailwind v4 tokens.** Study Mosaic's layout, palette, type scale, spacing, radii, shadows and patterns in its repository and demo, then write our own components from scratch. Matching colour values and layout ideas is fine; copying code is not. Deal-breaker only if the hand-built interactive parts (menus, drawer) are not accessible, which the DS-01 tests check.

## Decision
Rebuild the look on our own tokens, with no UI kit and no Mosaic source in the repository. Mosaic Lite by Cruip (github.com/cruip/tailwind-dashboard-template) is credited here as the visual inspiration.

- **Tokens.** `styles.css` keeps the `--tg-*` variables and adds a Mosaic-style palette: Mosaic's gray scale for surfaces, borders and text; a violet accent; success, warning, info and danger colours; a card shadow; the header height. Text and accent colours sit one step darker than Mosaic's in light mode (and lighter in dark mode) so every pairing meets WCAG AA (4.5:1 for text); for example the accent is `#5d47de` on white, 6.1:1, where Mosaic's `#8470ff` is 3.7:1.
- **Theme.** Light, dark or the system's choice (the default), picked in the account menu and remembered per device (`uiStore.theme`, localStorage `tumnis.theme`). A manual choice sets `data-theme` on `<html>`; "system" removes it and the CSS follows `prefers-color-scheme`. The `dark:` variant is redefined to follow the theme in force. `public/theme-init.js`, a same-origin file because the CSP forbids inline script, applies the stored choice before the first paint.
- **Type.** Inter, self-hosted from `@fontsource-variable/inter` (pinned), as `--font-sans`. The Latin file is precached for the offline shell; other scripts load on first use through `unicode-range`.
- **Shell.** On a laptop a sidebar with the same destinations in two groups, collapsible to icons (remembered per device, `uiStore.sidebarCollapsed`). A sticky header with search (opens the Mod+K palette through `uiStore.searchOpen`), the review queue with its count (the one badge, nothing shown at zero), help with the keyboard shortcuts, and an account menu (settings, theme, sign out). On a phone the bottom bar stays in thumb reach and the header's menu button opens a modal drawer with the grouped links (focus trapped, Escape closes, the page behind inert). No destination was added for the look.
- **Primitives.** Menus and the drawer are small hand-written components following the WAI-ARIA menu button and modal dialog patterns. A Radix primitive can still replace one when a screen needs more than they do (ADR-0004).
- **Cards and charts** follow in DS-01's later PRs: rounded, bordered cards with header rows within the PRD's one-screen and five-task limits, and a Chart.js wrapper loaded only through a dynamic import on pages that already have metrics.

## Consequences
- The whole app restyles through the tokens; pages in flight on other branches pick up the look without edits.
- We own the accessibility of the menu, popover and drawer; their keyboard behaviour is covered by T-DS-01-03 to T-DS-01-07 (Vitest) and T-DS-01-08 to T-DS-01-12 (Playwright at 375 and 1280 px, including axe in dark mode).
- The initial JavaScript grows only by the shell's own code; fonts are CSS assets and do not count against the 200 KB budget.
- The header adds 4 rem at the top; a page that fills the screen (the dashboard) subtracts `--tg-header-h`.
- No Mosaic code, CSS or images enter the repository, so its licence places no terms on ours.

## Sources
- [Mosaic Lite by Cruip](https://github.com/cruip/tailwind-dashboard-template) (visual inspiration only; its README states the licence and the no-redistribution term)
- [Tailwind CSS: dark mode, toggling manually with a data attribute](https://tailwindcss.com/docs/dark-mode)
- [Tailwind CSS: adding custom variants](https://tailwindcss.com/docs/adding-custom-styles#adding-custom-variants)
- [Fontsource: Inter variable](https://fontsource.org/fonts/inter/install)
- [WAI-ARIA APG: menu button pattern](https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/)
- [WAI-ARIA APG: modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
- [WCAG 2.2: contrast (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
