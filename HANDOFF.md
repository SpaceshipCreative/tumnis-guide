# HANDOFF: P2-11 Prompt-injection regression set

The coordinator's context watcher sent "HANDOFF NOW". There's no PR yet. The branch is
`wp/P2-11` on origin. The local branch is still the throwaway `worktree-agent-a78fe13f5dca6eef8`
because `git branch -m` was refused (read-only .git/config). Always push with
`/usr/bin/git push origin HEAD:wp/P2-11`. Keep scratch files in `$TMPDIR/P2-11-c0/`.

## Commits (on top of main cf20116)

| SHA | What |
| --- | --- |
| a581266 | test(profiles): P2-11 spec tests (red). Also adds the fixtures (18 cases + twins, the PDF and make_pdfs.py), name stubs, mcp==2.2.0 in profiles, and the spec-guard glob for `backend/fixtures/hostile/*.yaml` with a new test in backend/tests/ci/test_spec_guard.py |
| 7217a54 | T-P2-11-06 green: `load_hostile` |
| c1012d9 | T-P2-11-02/03 green: `judge.py` (+ `mock_worker_tools.action_class`; `run._validator` renamed to `run.validator`) |
| 8d30139 | T-P2-11-07 green: `mock_mcp_min.py`, `mock_worker_tools.py`, plus a new worker-mock test |
| aaa18d2 | T-P2-11-04 green: `discover_skills`, `coverage_gaps`, injection (`BASES`), `expand`, `changed`, `render_index` |
| a9fab3e | `hostile_run.py` (the homelab runner), CLI (`run --suite hostile`, `coverage`, `index`), the pytest collector for `tests/cases/hostile/index.yaml`, `cases.load_cases` skipping `hostile/`, `HermesRunner.install/attempt` gain `source=`/`slot=`, generated index, T-05 helper `backend/tests/_hostile.py`. **T-05's marker is removed but the test has NOT been run yet** |
| (this) | chore: P2-11 handoff |

## Spec tests

- Green, marker removed: T-02, T-03, T-04, T-06, T-07 (profiles/harness/tests), plus the non-spec tests `test_pdf_cases_are_reproducible_and_hide_their_text` and `test_worker_mocks_record_calls_and_map_action_classes`.
- T-P2-11-05 (`backend/tests/integration/test_hostile_packets.py`): marker removed, never run. The background `make test-int` finished after the handoff with most integration tests ERRORing at setup. The cause was Docker API 500s when starting containers on the loaded VM, across every module, so it was environmental and says nothing about T-05. **First step: run `make test-int` (bare, from the worktree root), or push and read CI's `integration` job.** If it fails, fix `backend/tests/_hostile.py` (the injection plumbing), never the test's assertions. Things that might go wrong:
  - an ingest step normalizes the invisible tag characters (case 5), so the containment check fails;
  - `link_context` `added_by="user"`;
  - `make_world` needs the db configured (`_integrations.configured(db)` is used).
- T-P2-11-01: the generated index `profiles/tests/cases/hostile/index.yaml`, one item per (case, skill), with meta `xfail: spec:P2-11`. It stays xfail until the homelab runner passes it (like P1-05's cases).

## Remaining steps

1. Verify T-05 (see above). Commit the fix if one is needed.
2. Add unit tests (profiles/harness/tests/test_hostile.py, non-spec), then commit:
   - the committed index equals `render_index(load_hostile(), discover_skills())` (or `python -m harness index --suite hostile --check`);
   - `render_prompt(recorded, unchanged body) == recorded prompt_text` for both base packets;
   - every expanded run's packet validates and contains the payload;
   - `expand(..., smoke=True)` picks smoke, changed skills and changed cases;
   - `changed()` maps paths;
   - `prepare_profile` rewrites mcp.json to the mocks only (no `jev` uvx, no real host);
   - `transcript_of` keeps non-`mcp_` stream calls as server `hermes`;
   - `run_suite` with a stub `run_once` gives 3 verdicts per run;
   - CLI: `coverage` exits 0; `run --suite hostile --runs 2` exits 2; `run --suite hostile` exits 2 while the model is UNSET.
3. CI wiring (coordinator decision (a), binding):
   - ci.yml `skills` job, STEPS ONLY. Add `backend/fixtures/hostile schemas/mcp backend/tumnis/modules/agents/packet_builder.py backend/tumnis/modules/agents/rules.py` to the "Did a skill input change?" diff list. Add `uv run python -m harness coverage --suite hostile` and `uv run python -m harness index --suite hostile --check` to the any-runner step. Add a homelab-gated step `uv run python -m harness run --suite hostile --smoke --base "$BASE_SHA" --junit "$RUNNER_TEMP/hostile-junit.xml"`. Don't touch the job set, timeouts or required-checks.txt (T-P0-03-16).
   - nightly.yml: a new `skills-hostile` job with `if: vars.HOMELAB_RUNNER == 'true'` (skipped, not failed, otherwise), `runs-on: [self-hosted, homelab, hermes]`, a `timeout-minutes` value, the same pinned checkout and setup-uv SHAs as ci.yml, and `uv sync` + `uv run python -m harness run --suite hostile --runs 3 --junit ...` in `profiles/`. It is not a required check. No test, since nothing tests nightly.yml's shape.
4. profiles/README.md: document the hostile commands. Any profile edit needs a VERSION bump (scripts/ci/profile_version_check.py); the harness is not a profile, so check whether README counts.
5. `make check` (set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` to files under `$TMPDIR/P2-11-c0/`), `make test`, `make test-int`.
6. Before pushing, run gitleaks over the new files: copy them to `$TMPDIR/P2-11-c0/leakscan`, then `docker run ... gitleaks dir /scan --config ...`. The `gitleaks git` form is refused by the worktree guard. The last scan (red commit) was clean.
7. Open the PR (body in `$TMPDIR/P2-11-c0/pr-body.md`), request `@coderabbitai review` once, run the review loop, then send "#<PR> MERGE-READY at <sha>" to main.
8. After P2-00 (wp/P2-00-spec) merges: merge main. Its `profiles/harness/tests/test_a2_7_hostile_suite.py` runs `python -m harness run --suite hostile --runs 3` (it exits 2 today because the model is UNSET, so the xfail holds). Gate it behind `--run-skills` (a skip when the option is absent, added in a way spec-guard allows, or ask the coordinator), and don't remove its marker unless it really passes.

## Decisions and deviations (for the PR body)

- The plan names `profiles.harness` and `.github/workflows/skills.yml`. The repo has the package `harness` (run from `profiles/`) and a `skills` job in ci.yml.
- The plan maps `proxmox.delete_vm` to `proxmox_destructive`. The code uses the project policy's actual names instead (`proxmox_delete_guest`, `proxmox_rollback_snapshot`, from projects.rules GATED_DEFAULT).
- Case field `forbidden_calls[].args_match` on `update_task_status` uses `to` (the catalogue's argument), not `status`.
- `expected_calls_on_twin: []` for every case. Phase 1 skills (enrich, plan) call no tools, and `post_result` isn't in the catalogue yet (P2-04). A twin still fails if it gives no valid JSON reply, which is what catches a refuse-everything skill. P2-12 adds expected calls with the skills that make them.
- Case additions to the plan's format:
  - `companions` (case 13's split payload: a note plus an email on the same task);
  - `pdf` (case 14's hidden-text PDF);
  - `version` and `meta` in the fixture index (meta holds T-P2-11-01 and `xfail: spec:P2-11`).
- `digest_entry` (case 18) is injected into packets as a comment written by an API key. P2-03 isn't merged, and that's how a digest's `task_commented` entry reaches a packet today. `passage` is added to the gathered inputs, because `gather_inputs` doesn't join passages until P1-17's `passages_for`.
- For phase 1 skills, injection goes into the skill's own recorded packet:
  - enrich: a task title replaces the task title; everything else becomes a passage;
  - plan: each part becomes an extra candidate, with a title or first_action.
- The mocks are stdio MCP servers (Hermes subprocesses), so they bind nothing, not even loopback. `mcp.json` in a hostile run replaces every server, `jev` included.
- The hostile CLI exits on the real verdict and ignores the index meta's xfail. That xfail marks only the pytest items, so A2.7 (P2-00) can't xpass falsely.
- The mock doesn't invent `request_approval` or `post_result`. They appear as soon as P2-04/05 put them in the catalogue. The default response policy answers `request_approval` with `pending`.
- Shared-file edits: `profiles/pyproject.toml` + `profiles/uv.lock` (mcp==2.2.0, same pin as jev-mcp), `scripts/ci/_tests_extract.py` (GOLDEN_GLOBS gains `backend/fixtures/hostile/*.yaml`).
- Docs cited: MCP Python SDK v2 low-level server and in-memory Client (Context7 /websites/py_sdk_modelcontextprotocol_io_v2; py.sdk.modelcontextprotocol.io/v2/advanced/low-level-server, /v2/migration, /v2/get-started/testing), and the Hermes MCP config reference (hermes-agent.nousresearch.com/docs/reference/mcp-config-reference: `mcpServers` stdio command/args/env, tool names `mcp__<server>__<tool>`).

## Scott items

- A2.7 is judged on the nightly `skills-hostile` run and stays red until the homelab runner exists: it needs `HOMELAB_RUNNER=true` and a pinned model in harness.toml and config.yaml.
- TDD steps 6 and 7 (real failures on enrich and plan, fixing SOUL/skill text) need the homelab runner.

## Verify

```
cd profiles && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
cd profiles && uv run python -m harness coverage --suite hostile && uv run python -m harness index --suite hostile --check
make test-int      # bare, from the worktree root (T-P2-11-05)
```
