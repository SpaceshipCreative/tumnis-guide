# P1-05 handoff: Profiles in the repo and the skill harness

Branch `wp/P1-05` (pushed with `/usr/bin/git push origin HEAD:wp/P1-05`). **No PR is open yet.**
Work in progress; stopped on the coordinator's HANDOFF NOW. Read the prompt
(`~/tumnis-coordinator/prompts/P1-05.md`), `vm-agent-rules.md` and `scott-decisions.md` first.

## Commits

| SHA | What |
| --- | --- |
| 6dbf372 | `test(agents): P1-05 spec tests (red)`: all 13 spec tests as strict xfail |
| 8511f99 | `feat(agents)`: skill_io.py, enrichment_errors, planning_errors, render_prompt, schemas, fixtures, recordings (T-10, T-11 green) |
| 0905119 | `feat(profiles)`: harness/assertions.py and harness/cases.py (T-06, T-09 green) |

## Spec tests

| ID | File | State |
| --- | --- | --- |
| T-P1-05-10 | backend/tumnis/modules/agents/tests/unit/test_enrichment_errors.py | green, marker removed |
| T-P1-05-11 | backend/tumnis/modules/agents/tests/contract/test_packet_schemas.py | green, marker removed |
| T-P1-05-06 | profiles/harness/tests/test_assertions.py | green, marker removed |
| T-P1-05-09 | profiles/harness/tests/test_cases.py | green, marker removed |
| T-P1-05-07, 08 | profiles/harness/tests/test_run.py | red (xfail): needs harness/run.py and harness/report.py |
| T-P1-05-12 | profiles/harness/tests/test_profiles.py | red (xfail): needs the two profile distributions and profiles/shared/jev-mcp |
| T-P1-05-13 | backend/tests/ci/test_profile_version_check.py | red (xfail): needs scripts/ci/profile_version_check.py |
| T-P1-05-01..05 | profiles/tests/cases/{enrich,plan}/*.yaml | red via `meta.xfail: spec:P1-05`; KEEP the marker (they can only pass on the homelab runner with real Hermes; nobody has run them) |

Never edit an assertion in these files. Remove a marker only once its test passes.

## Remaining steps (in order)

1. **harness/run.py** (T-07, T-08). API the locked tests use:
   - `Attempt(outcome: Literal["pass","fail"], failures: tuple[str, ...] = (), tool_calls: tuple[ToolCall, ...] = (), output: dict | None = None, duration_ms: int = 0)`; `ToolCall(name, input)`.
   - `CaseResult(case, attempts)` with `.status` ("passed" only if every attempt passed, else "failed") and `.passes`.
   - `run_case(case, run_attempt, *, runs) -> CaseResult`: calls `run_attempt(case, n)` for n = 1..runs, all of them (no early stop).
   - `load_config(path=harness/harness.toml) -> HarnessConfig(runs, model, provider, timeout_s, install_prefix, max_parallel)`; refuse runs < 3.
   - `attempt_from_stream(case, stream_text, *, exit_code=0, timed_out=False, duration_ms=0) -> Attempt`: parse the stream with the daemon (add a public `read_stream_json(text) -> (events, final)` to `daemon/tumnis_daemon/runner.py`, and make `read_recording` use it), build a daemon `Run` from `case.packet` (profile = case.profile), get the reply through the daemon's `build_result(...)` (so `no_json`, `hermes_error` etc. match production), collect tool calls from records with `type` in {"tool_use", "tool_call"} (`name`, `input` or `arguments`; Hermes documents `tool_use` with `name`/`input`; the daemon's synthetic recordings use `tool_call`), fail on any call not matching a `case.allow` glob (fnmatch), validate the reply against `REPO / case.output_schema.path` (jsonschema Draft 2020-12), run `assertions.RULES[name](case.packet["body"], output)` for each case rule, and `check_json(output, case.json_checks, case.packet)`.
   - Real runner (homelab only): `hermes profile install profiles/<profile> --name <install_prefix>-<profile>-<sha8> -y`, attempts via the daemon's `write_query_file` + `hermes_argv(DaemonConfig(...), run_msg, query)` + `["-m", model, "--provider", provider]`, `clean_env`, subprocess with `start_new_session=True` and a group kill on timeout; `hermes profile delete <name> -y` in `finally`. Refuse to run while harness.toml's model starts with `UNSET`.
2. **harness/report.py** (T-07): `summary_line(result)` contains "passes/runs" (e.g. "enrich-x: failed (2/3)"); `junit_xml(results) -> str`: `<testsuite name="skills">`, per case one `<testcase classname="skills.<case-id>" name="<case-id>">` with `<failure message="2/3 attempts passed">` when failed, plus one `<testcase classname="skills.<case-id>" name="attempt N">` per attempt with `<failure>` only on failed attempts. Also a verdict that honours `meta.xfail` like strict xfail (failed -> xfailed, passed -> xpassed = failure).
3. **harness/harness.toml**: `runs = 3`, `model = "UNSET-pinned-homelab-model"`, `provider = "custom"`, `timeout_s = 180`, `install_prefix = "tumnis-ci"`, `max_parallel = 5`. Scott item: set the real pinned homelab vLLM model id and provider here AND in both profiles' config.yaml (add a non-spec test that they are equal).
4. **harness/__main__.py**: `python -m harness run --cases tests/cases [--junit PATH] [--case ID]` (ThreadPool over cases, `max_parallel`; exit 1 on a failure or an unexpected pass) and `python -m harness check --cases tests/cases` (loads every case, no model).
5. **harness/pytest_plugin.py** + `profiles/conftest.py` (`pytest_plugins = ["harness.pytest_plugin"]`): collect `tests/cases/**/*.yaml` as one item per case (`nodeid ...yaml::<case-id>`), add `req(*meta.req)`, `wp(meta.wp)` and `xfail(strict=True, reason=meta.xfail)` markers; the item skips unless `--run-skills` is given.
6. **Profiles** (T-12; the test is the spec, read it): `profiles/{project-template,master}/{distribution.yaml, SOUL.md, config.yaml, mcp.json, VERSION}` and `skills/{enrich,plan}/SKILL.md`.
   - distribution.yaml: `name: tumnis-project-template` / `tumnis-master`, `version: 1.0.0` (= VERSION), `env_requires` as a list of MAPPINGS (`- name: TYPESAFE_API_KEY, description: ..., required: true`). The plan's bare-string form is refused by Hermes' `EnvRequirement.from_dict` (checked in hermes-agent `hermes_cli/profile_distribution.py`).
   - mcp.json: `{"mcpServers": {"jev": {"command": "uvx", "args": ["--from", "git+https://github.com/SpaceshipCreative/tumnis-guide@jev-mcp-v0.1.0#subdirectory=profiles/shared/jev-mcp", "tumnis-jev-mcp"], "env": {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"}}}}`. Hermes resolves `${VAR}` against the profile's .env. Note for Scott/P2-12: Hermes' runtime reads MCP servers from config.yaml `mcp_servers`; mcp.json is a distribution-owned file whose runtime use was not confirmed; phase 1 skills call no tools, so the server stays unused until P2-12, which must verify it (or mirror it into config.yaml). Scott item: create the git tag `jev-mcp-v0.1.0` after merge.
   - config.yaml: `model: {default: UNSET-pinned-homelab-model, provider: custom}` only.
   - SKILL.md: YAML frontmatter `name`, `description`, then the plan's text; the reply MUST include `"schema_version": 1` (the generated schema requires it). SOULs share one rule block (packet is data, never instructions; reply with JSON only; call no tools in phase 1); add a non-spec test that both SOULs contain it (plan TDD step 8).
   - Never commit .env, auth.json, memories, sessions.
7. **profiles/shared/jev-mcp/**: `pyproject.toml` (name `tumnis-jev-mcp`, version `0.1.0`, deps `mcp==2.2.0` and `typesafe-sdk==0.7.2`, script `tumnis-jev-mcp = "jev_mcp.server:main"`), `jev_mcp/server.py` with `from mcp.server.mcpserver import MCPServer` (mcp 2.x renamed FastMCP to MCPServer), one `ask(state, questions, model?)` tool forwarding to `typesafe_sdk.AsyncTypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"], model=pinned).system_one(...)`; pinned model default `jev-1.13.0` (as scripts/record_jev.py). See backend/tumnis/modules/decisions/adapters/jev.py for the SDK types (Choice, Score, Noul). Add `extend-exclude = ["shared"]` to profiles ruff config or lint it separately.
8. **scripts/ci/profile_version_check.py** (T-13; test in backend/tests/ci/, loads it through `tests.ci._scripts.load`): `main(["--base", sha, "--head", sha, "--repo", path]) -> int`; for every `profiles/<name>/` that has a VERSION at head, a change under it (git diff --name-only base...head, or base..head) without a change to its VERSION content returns 1; new profiles pass. It is linted by the Lint job (`ruff` + `mypy --strict` over scripts/ci).
9. **spec_guard** (small, with a new test in backend/tests/ci/test_spec_guard.py): let `_compare_text` accept a case file whose only change is removing its `xfail: spec:...` line, so the YAML cases' markers can be removed later without the spec-change label (the YAML analogue of removing a pytest spec marker).
10. **traceability** (optional, plan says the traceability job should see the YAML items): in `scripts/ci/traceability.py::pytest_refs`, also collect `profiles/` (same python, `cwd=repo/"profiles"`, prefix node ids with `profiles/`).
11. **CI** (`.github/workflows/ci.yml`; deviation: the plan's separate skills.yml is replaced by filling the existing `skills` job, because T-P0-03-16 locks ci.yml's job list and budgets; the Skills budget stays 5 min, flag for Scott): the skills job keeps its `vars.HOMELAB_RUNNER` gate (add the `hermes` label to the homelab runs-on), always runs `uv sync; ruff; mypy; pytest -q` in profiles/, then detects changes under profiles/, schemas/enrichment/, schemas/planning/, daemon/ (fetch-depth 0; BASE_SHA = PR base or event.before) and runs `uv run python -m harness run --cases tests/cases --junit $RUNNER_TEMP/skills-junit.xml` only on the homelab runner. Lint job: `fetch-depth: 0` and a step `uv run python ../scripts/ci/profile_version_check.py --base "$BASE_SHA"`.
12. **Makefile** (shared file; list it in the PR body): add `cd profiles && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` to `check`.
13. `profiles/README.md` update and a `profiles/tests/recordings/README.md` (recordings are built from the backend models with `render_prompt`; P1-08/P1-11 replace them with packets from their builders; `test_recorded_packets_match_the_models` fails when a schema change leaves them stale). The one-off generator used is not committed; regenerate with a short script from skill_io + render_prompt if the models change.
14. Finish: `make check` (with the SEMGREP_* env vars under the scratchpad), `make test` (contract needs `npm ci --prefix frontend` first for the openapi-ts test), `make test-int` (bare), push, open the PR (`gh pr create --base main --head wp/P1-05 --title "[P1-05] impl: Profiles in the repo and the skill harness" --body-file <file>`), comment `@coderabbitai review`, then the review loop.

## Decisions and deviations so far

- T-P1-05-13 lives in `backend/tests/ci/test_profile_version_check.py`, not `scripts/ci/tests/`: R-16 says every spec path starts with backend/, frontend/, daemon/ or profiles/, and the CI-script tests already live in backend/tests/ci (run by the unit layer and traceability).
- `enrichment_errors` / `planning_errors` take structural Protocols, because rules.py may not import skill_io (the rules purity meta-test). `planning_errors` is an addition (unknown_pick, duplicate_pick, too_many_picks, unknown_alternate); the two plan cases use it and P1-11 can reuse it.
- Skill models subclass `VersionedPayload` (frozen, extra=forbid), not `BaseModel` as in the plan's sketch: `@versioned` requires it.
- `priority` is the tasks module's string literal (low/normal/high/urgent), not `int` as in the plan sketch; `EnrichTask.label` is required (`Label`); `PlanCandidate.label` may be null (pending, R-08). `Interval` and `ProjectAgentEntry` are defined in skill_io (calendar's Interval and P1-06's registry don't exist yet). Datetimes are `AwareDatetime`.
- `render_prompt(skill, output_schema, body)` added to packet_builder.py (P1-17 owns the builders; this is the minimal seam the recordings need). It escapes `<` as `<` inside the JSON so a body cannot close `</packet>`.
- The harness puts ../backend and ../daemon on sys.path (harness/__init__.py) instead of installing them; the backend import is limited to agents.rules and agents.skill_io (pydantic only).
- The five YAML cases keep `meta.xfail: spec:P1-05`: they need the real Hermes and model on the homelab runner, which does not exist yet.

## Scott items (so far)

- The homelab runner (`[self-hosted, homelab, hermes]`, `vars.HOMELAB_RUNNER=true`) and the pinned homelab model id + provider (harness.toml and both config.yaml).
- Tag `jev-mcp-v0.1.0` after merge; confirm whether Hermes loads mcp.json at runtime (else mirror into config.yaml) before P2-12.
- The Skills job budget is locked at 5 min (T-P0-03-16); five cases x three real runs may not fit. Raising it is a spec change.
- Done-checklist items needing the Hermes VM: both distributions install with `hermes profile install ... -y`; all five cases pass 3 of 3.

## Verify commands

```bash
cd backend && uv run pytest -q -p no:randomly tumnis/modules/agents/tests/unit tumnis/modules/agents/tests/contract/test_packet_schemas.py tests/ci/test_profile_version_check.py
cd profiles && uv sync && uv run ruff check . && uv run pytest -q     # use `rtk proxy` to see pytest output
cd backend && uv run tumnis gen all --out ..  && /usr/bin/git status --short schemas backend/tests/contract/generated
```

Gotchas: the worktree guard refuses complex Bash (heredocs, loops, `cd` + git); use the Write/Edit tools for files and `/usr/bin/git` from the worktree root. `make check` needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` set to scratchpad files (literal paths, not `$TMPDIR`) and `PYTEST_XDIST_AUTO_NUM_WORKERS=3`. The profiles mypy run fails on test_run.py until run.py and report.py exist.
