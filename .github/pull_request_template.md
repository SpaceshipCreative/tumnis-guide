<!-- Title: [<WP>] spec: <title>  or  [<WP>] impl: <title> (AGENTS.md). -->

## Work package

WP: <!-- e.g. P0-18; add the wp:<WP> label -->
Requirements: <!-- PRD IDs this PR proves, e.g. FR-3.1, REL-2 -->
Fixes: <!-- bug PRs: "Fixes #<n>", with a new test named test_issue_<n>_... (red-proof) -->

## Tests

<!-- Spec tests added or turned green (T-IDs), and what else changed in the test suites. -->

| Layer | Result |
| --- | --- |
| `make check` (lint + unit) | |
| `make test-int` | |
| `make e2e` | |

## Done checklist

- [ ] Spec tests merged red first; markers removed only as their tests pass
- [ ] No assertion edited and no test deleted (spec-guard), or Scott added `spec-change`
- [ ] Every new test has `req` and `wp` tags (traceability)
- [ ] Coverage gates hold (80% rules and MCP; 100% planner, focus, threshold, state machine)
- [ ] New adapter: fake and contract suite; new module: import-linter contract
- [ ] `make gen` output committed; UI changes checked at 375 px
- [ ] New shared names added to Part A of the plan
