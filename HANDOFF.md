# HANDOFF: P2-00 (phase 2 acceptance suite spec PR)

Branch `wp/P2-00-spec` (pushed with `/usr/bin/git push origin HEAD:wp/P2-00-spec`), based on
main at 7083652 (#104). Task prompt: `/tmp/claude-1002/coord7/p2-00.txt`. Scratch:
`$TMPDIR/P2-00-c0/`. **No PR is open yet; no CodeRabbit review; no CI run yet.**

## Done (commits)

| SHA | What |
| --- | --- |
| 883601c | `test(acceptance)`: `backend/tests/acceptance/_phase2.py` (helpers, no assertions) + A2.2, A2.3, A2.4, A2.5 pytest files |
| 83aca30 | `test(e2e)`: `frontend/e2e/phase2.ts`, `testHooks.ts` (runner.script(taskTitle, runs), runner.lastPacket()), J3.spec.ts (A2.1), acceptance/A2.2-question.spec.ts, J8.spec.ts (A2.6); `ajv` 8.20.0 pinned as a direct devDependency (package.json + one line in package-lock.json root; the package was already in the lock as a transitive dev dep) |
| 0b520cd | `test(profiles)`: `profiles/harness/tests/test_a2_7_hostile_suite.py` (A2.7) |
| 92f86fb | `docs(plan)`: phase 2 `status: active` in docs/plan/work-packages.yaml (as PR #43 did for phase 1) |

`make check` passes (with SEMGREP_SETTINGS_FILE / SEMGREP_LOG_FILE / SEMGREP_VERSION_CACHE_PATH under
`$TMPDIR/P2-00-c0/`, the known read-only `~/.semgrep` issue). Frontend `tsc`, ESLint, Prettier clean.
`npx playwright test --list` lists 6 tests (3 specs x phone/laptop). Backend acceptance collects
(`uv run pytest --collect-only -m integration tests/acceptance`). Profiles: A2.7 is 1 xfailed
(`python -m harness run --suite hostile --runs 3` exits 2: unknown `--suite`).
Integration and Playwright were NOT run locally (VM loaded; CI judges).

## The suite

| Row | File | Owning WP (marker) | Red reason today |
| --- | --- | --- | --- |
| A2.1 | `frontend/e2e/journeys/J3.spec.ts` | P2-04 (`test.fail()`) | `POST /v1/test/fakes/runner/script` 404 `unknown_fake` (no runner script hook), then Run button / run view / result review missing |
| A2.2 API | `backend/tests/acceptance/test_a2_2_question_resumes_run.py` | `xfail(strict, "spec:P2-05")` | `agents.api.configure_human_waits` missing (settings override), then `POST /v1/tasks/{id}/run` (P2-04), `ask_human` (P2-05) |
| A2.2 UI | `frontend/e2e/acceptance/A2.2-question.spec.ts` | P2-05 (`test.fail()`) | runner script hook 404, question review item missing |
| A2.3 | `backend/tests/acceptance/test_a2_3_approvals_and_taint.py` | `spec:P2-05` | same as A2.2 API, then `request_approval` |
| A2.4 | `backend/tests/acceptance/test_a2_4_delegation_wait.py` | `spec:P2-06` | `configure_human_waits`, then `delegate_task`/`wait_for_task` |
| A2.5 | `backend/tests/acceptance/test_a2_5_kill_switch.py` | `spec:P2-09` | `POST /v1/tasks/{id}/run` (P2-04), then pause/resume routes |
| A2.6 | `frontend/e2e/journeys/J8.spec.ts` | P2-15 (`test.fail()`) | runner script hook 404 (plan publish), then focus routes/bar |
| A2.7 | `profiles/harness/tests/test_a2_7_hostile_suite.py` | `spec:P2-11` | harness refuses `--suite hostile` |

Nothing is "already green": none of P2-04/05/06/09/11/15 is merged.

## Remaining steps

1. `/usr/bin/git fetch origin` and `/usr/bin/git merge origin/main` if main moved; rerun `make check` (semgrep env vars as above).
2. PR body file in `$TMPDIR/P2-00-c0/pr-body.md`: the table above; harness gaps; deviations; docs cited; ending with a blank line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
3. `gh label create wp:phase-2` if missing; `gh pr create --base main --head wp/P2-00-spec --title "[P2-00] spec: phase 2 acceptance suite" --label wp:phase-2 --body-file <file>`; then `gh pr comment <url> --body "@coderabbitai review"` (once; quota tight).
4. Review loop per `~/tumnis-coordinator/pr-review-loop.md`; CI green (preview pending is fine; if `contract` is cancelled, `gh run rerun <id> --failed` once). Expected CI: backend integration 4 xfailed in tests/acceptance for A2.x; e2e 6 expected failures; skills job profiles pytest 1 xfailed.
5. SendMessage to `main`: `#<PR> MERGE-READY at <sha>`. Never merge.

## Harness gaps (for the PR body; built by the owning WPs, not here)

- `POST /v1/test/fakes/runner/script` for `{task_title, runs: [[steps...], ...]}` (steps: `stream`, `upload_artifact`, `ask_human`, `result`) and `GET /v1/test/fakes/runner/last-packet` answering `{packet, run_messages}` (P2-04; R-37).
- `agents.api.configure_human_waits(poll_seconds=)` (the plan's `settings_override(human_wait_poll_seconds=2)`, P2-05).
- `task_token_client` / `master_key_client` fixtures: the tests take the token from the `run` packet and make the master key in `_phase2.arrange_world(master=True)`.
- `POST /v1/test/tick/focus-wake`, `PUT /v1/focus/level`, master plan script `plan__write_proposal_block` (P2-15).
- `harness run --suite hostile --runs 3`, hostile cases and judge (P2-11); P2-11 must gate A2.7 behind `--run-skills` when it turns green.
- Seed rows: `Acme site` / `Fix footer link` (with a tainted email and a brief) and `Write proposal` are not in `backend/fixtures/seed` (same gap as PR #43).

## Deviations from the plan (put in the PR body)

1. Backend fixtures: the plan names `seed` (and `task_token_client`, `key_client`); `session_client`, `key_client` and `fake_runner` serve the `workspace` fixture's workspace, not the seed's, so `_phase2.arrange_world` makes the plan's rows (Acme site, acme-site on a protocol-2 fake runner, its token key; the master and its key) in that workspace. The tests play the agent over MCP (through the runner's TestClient, whose lifespan runs the MCP session manager) and over the runner socket.
2. Tainted email: linked as a `url` context item (tainted by rule) on `mail.example.com`; the owning WP may swap in an ingested message.
3. A2.2: the plan's answer field is `question_id`, P2-05's model says `id`; `_phase2.wait_id` reads either.
4. A2.7: the plan's files (`profiles/tests/cases/hostile/`, `backend/fixtures/hostile/**`) are P2-11 deliverables and the harness refuses unknown case keys (`expected_fail`, `acceptance`) and rglobs every YAML under `tests/cases`; so A2.7 is a pytest test running the plan's command, `xfail(strict, "spec:P2-11")`, the same convention the harness implements (`meta.xfail: spec:<WP>`).
5. `ajv` 8.20.0 added as a direct devDependency (shared file `frontend/package.json`) to validate the packet against `schemas/packet/v1/task_packet.json` (Ajv2020, `strict: false`, `validateFormats: false`, matching the backend's jsonschema check).
6. Phase 2 set to `status: active` in work-packages.yaml.

## Docs cited

- Playwright 1.63 clock (Context7 `/microsoft/playwright/v1.63.0`, class-clock.md: `install`, `fastForward`, `setSystemTime`).
- Ajv 8 draft 2020-12 (`ajv/dist/2020`, Context7 `/ajv-validator/ajv`, docs/json-schema.md); verified the named ESM import `{ Ajv2020 }` loads under Node.

## Scott items

- None blocking. Seed rows for Acme site / Fix footer link / Write proposal are an owning-WP gap.

## Verify commands

```bash
SEMGREP_SETTINGS_FILE=$TMPDIR/P2-00-c0/semgrep-settings.yml SEMGREP_LOG_FILE=$TMPDIR/P2-00-c0/semgrep.log SEMGREP_VERSION_CACHE_PATH=$TMPDIR/P2-00-c0/semgrep-version make check
cd backend && uv run pytest --collect-only -q -m integration tests/acceptance
cd frontend && E2E_BASE_URL=http://localhost:1 npx playwright test --list e2e/journeys/J3.spec.ts e2e/journeys/J8.spec.ts e2e/acceptance/A2.2-question.spec.ts
cd profiles && uv run pytest -q -rxX -k a2_7
```
