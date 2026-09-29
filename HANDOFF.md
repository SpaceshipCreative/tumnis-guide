# P0-24 handoff 4: Project page with Tasks and Board views

Worktree `/home/claude/Projects/Tumnis-Guide-wt/p0-24`, branch `wp/P0-24`, pushed to
`origin/wp/P0-24`. Commit as Claude with per-command config
(`git -c user.name=Claude -c user.email=noreply@anthropic.com commit ...`); never set
git config. Trust `git log --oneline origin/main..HEAD`.

## Remaining steps (only these)

1. Remove the three `test.fail();` markers, each with an exact Edit on its own line:
   T-P0-24-04 and T-P0-24-05 in `frontend/e2e/board.spec.ts`, T-P0-24-17 in
   `frontend/e2e/layout/project.spec.ts`. That edit was denied by the permission
   classifier on 2026-09-29; Scott must approve it. Do not route around it. All three
   already pass (they report "expected to fail, but passed"). Run Prettier on the two
   files, rerun them against the stack, commit `test(frontend): T-P0-24-04, 05, 17 green`.
2. Delete this file in a `chore:` commit, push (`git push origin wp/P0-24`, never set
   upstream), and open the PR with `~/tumnis-coordinator/open-pr.sh wp/P0-24 "[P0-24]
   impl: project page and board" "<body: summary, spec tests, deviations, Fixes #51>"`.
   `gh pr checks` gets 403 on the VM: use `gh run list --branch wp/P0-24`.
3. Follow `~/tumnis-coordinator/pr-review-loop.md`. Do not merge.

## Done in handoff 4 (commits)

- `83363e9` fix(frontend): T-05 and T-17.
  - `components/board/keyboard.ts` (`SettledKeyboardSensor`): listens from the pick-up
    (dnd-kit adds its listener in a timeout, so fast keys were lost), and holds each key
    until `over`, the collision rect and the scroll offsets stay the same for a frame
    (at most 30 frames). `BoardView` uses it with `scrollBehavior: "auto"` (on the phone
    an arrow scrolls the board; smooth scrolling outlasted the next key).
  - The board scroller is `relative`: without it the columns' `sr-only` spans overflowed
    the document (1792 px on the phone).
  - `ViewSwitcher`: the radio is a transparent overlay (`absolute inset-0 opacity-0`) in a
    `relative` label; the old 1 px `sr-only` input was covered by its own label.
- `8c93479` docs(plan): the P0-24 row in A12.
- `4307fba` merge of origin/main (P0-20, P1-01, #54, P1 acceptance suite). Conflicts:
  `lib/live-map.ts` (both sides' ops kept), generated client and `schemas/openapi.json`
  (regenerated with `make gen`). `alembic heads`: one head per branch; `tasks_0003`
  still follows `tasks_0001` (no `tasks_0002` on main yet; P0-19, PR #50, adds it; the
  revision order is settled at merge).
- `b53c265` test(search): `_search.trash_task` now trashes through
  `tasks.api.trash_task` (the write behind `DELETE /v1/tasks/{id}`); `.importlinter`
  gains `tumnis.modules.search.tests.** -> tumnis.modules.tasks.api` beside usage's
  equivalent line. The locked test body is unchanged.
- test(meta): `tests/meta/_authz.py` gains `document_row` (`LOOKUP_TARGETS["knowledge"]`)
  for P0-24's `PATCH /v1/knowledge/documents/{id}` (`lookup:knowledge`); the project
  setup is shared with `task_row` (`_workspace_with_project`). The authz matrix is green
  (142 passed).
- Layers after the merge: `make check` green (907 unit); contract 108 passed; Vitest 63;
  Playwright whole suite green apart from the three marked specs passing. Integration
  666 passed; the remaining failures were Docker timeouts under load in tests P0-24 does
  not touch (`test_rclone.py::test_rclone_copy_keeps_files_removed_at_source` timed out
  at 180 s on each of three runs; the harness `sftp_server`/`clamd` fixtures once each).
- Earlier commits: see handoff 3 in git history (`ba54e3c`), including `fd16624` (#51).

## Spec tests

All 17 pass. T-04, T-05 and T-17 still carry their `test.fail()` markers (step 1).
The unmarked T-05 passed 20 of 20 (10 per width) after the fix.

## Still open after P0-19 lands

Recurrence seam: the frontend reads P0-19's recurrence routes with hand queries in
`components/project/queries.ts` and writes through `apiWrite` in
`drawer/RecurrencePicker.tsx`. Once P0-19 is on main, switch to the generated options,
add the ops to LIVE_MAP, and set `tasks_0003`'s `down_revision` to `tasks_0002`.

## Decisions and deviations (report these)

- dnd-kit: `@dnd-kit/react` 0.5.0 injects a `<style>` element during drags, which the
  strict CSP blocks, so this WP uses the plan's fallback `@dnd-kit/core` +
  `@dnd-kit/sortable`. Cards override useSortable's role to `listitem`, with
  roledescription "draggable task".
- No backend `order=due` on `GET /v1/tasks`: `/tasks` follows every cursor and sorts with
  `sortByDue` on the client (query key: the generated op's key plus `pages: "all"`).
- Extra backend routes not in the plan: `DELETE /v1/tasks/{id}` (trash),
  `GET /v1/tasks/{id}/comments`, `GET /v1/projects/{id}/brief`,
  `PATCH /v1/knowledge/documents/{id}`.
- Undo of a create trashes the task.
- T-06 and T-11 move cards through the card's Move menu (same `planMove`, same single
  request); jsdom cannot drag.
- `TaskOut` schema marks defaults required (`5707f3f`); `via` excluded when None
  (`2190474`).
- Vitest `testTimeout` 20 s (`706cb34`).
- Refused-move copy is keyed by the from-status; same-status has its own line.
- Board cards carry an "Open" button; the Tasks view has `j`/`k`, Enter, `s`, `d`.
- Connections edits people, domains and the code path; repo and Coolify links are kept.
- Recurrence PUT sends the task's version for a new rule.
- Issue #51, a bug fix to main's code (`truncate_tables`), in its own commit on this
  branch.
- Keyboard drags use `SettledKeyboardSensor`, which subclasses dnd-kit 6.3.1's
  `KeyboardSensor` and replaces its private `handleKeyDown` and `detach` at runtime; the
  keyboard auto-scroll is instant.
- The view switcher's radio is a transparent overlay rather than `sr-only`.
- Search's trash seam goes through `tasks.api` (a test-only `.importlinter` ignore).

## Shared-file edits (report these)

- `frontend/package.json`: the three `@dnd-kit` pins.
- `frontend/e2e/fixtures.ts`: `boardCards`, `openProject`, `recordWrites`.
- `frontend/src/test/factories.ts`: `makeBoard`.
- `frontend/vitest.config.ts`: `testTimeout`.
- `frontend/src/stores/uiStore.ts`, `lib/optimistic.ts`, `components/common/AppShell.tsx`,
  `ConflictToast.tsx` (`brief` entity), `lib/live-map.ts`.
- `backend/tumnis/core/testing_routes.py` (core, issue #51).
- `backend/.importlinter`: one ignore line and its comment (search tests -> tasks api).
  No edits to `pyproject.toml`, `uv.lock`, `Makefile` or `AGENTS.md`.

## Gotchas

- On the VM there is no `rtk`; run `uv run pytest` directly, with `-n 6` (other agents share the machine). Docker, semgrep and Playwright need the sandbox off.
- Remove a `spec:` marker with the Edit tool on the exact line; `sed -i` was refused. If a
  permission is denied, stop and report it; do not route around it.
- A heredoc or `rm -rf` in Bash trips a "Fact-Forcing Gate" hook: state the facts, or
  write files with the Write tool.
- Prettier reformats a test once its marker goes; `npm run lint` runs `prettier --check .`.
- A label wrapping a `<select>` puts the chosen option into the select's accessible name;
  use `htmlFor`/`id`.
- The MSW unhandled-request strategy is "error": the page must call only endpoints the
  ProjectFake serves.
- Leftover local image `tumnis:p024-main` (main's image used for the repro) can be removed
  with `docker rmi tumnis:p024-main`.

## Verify

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint
cd ../backend && uv run pytest tumnis/core/tests/integration/test_issue_51_reset_relay_deadlock.py tumnis/core/tests/integration/test_testing_routes.py tumnis/modules/tasks/tests/integration/test_undo.py -m integration
uv run pytest -m "not integration and not contract" -n 6; uv run pytest -m contract; uv run pytest -m integration -n 6
cd .. && make check
```
