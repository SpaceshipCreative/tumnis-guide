# HANDOFF: fix/ci-unit-speed (make the `unit` CI job faster, same 3-minute budget)

Stopped on the coordinator's "STOP NOW (VM out of RAM)" message. No PR is open yet and no CI
change is committed. This file is the only commit on the branch beyond main (64764e9).

## Branch
- Worktree branch `fix/ci-unit-speed`, at origin/main 64764e9 plus this HANDOFF commit.
- Push with `/usr/bin/git push origin HEAD:fix/ci-unit-speed`.
- Scratch lives in `$TMPDIR/ci-unit-speed/` (= /tmp/claude-1002/ci-unit-speed/). It holds `steps.py`, which prints a job's step
  durations: `python3 steps.py unit <run-id>...`.

## Constraints found
- `backend/tests/ci/test_ci_config.py` (T-P0-03-16, locked) requires:
  - the job set in ci.yml == required-checks.txt == BUDGETS;
  - `unit` timeout-minutes == 3;
  - no `name:` on any job.
- So we can't add jobs, a matrix or an aggregating job without a spec change. All speed-ups have
  to stay inside the single `unit` job.
- Already in place: setup-uv `enable-cache` keyed on backend/uv.lock, the setup-node npm cache
  keyed on frontend/package-lock.json, and pytest `-n auto` (4 workers on the current 4-vCPU
  public-repo runner).
- Vitest runs `maxWorkers: Math.max(2, availableParallelism() - 1)`, which is 3 workers on CI.
- NOTE: memory says the repo may go private again, which means 2-vCPU runners and a slower job.
  Mention this in the PR.

## Baseline timings (main, unit job)
| step | run 36715091184 (64f4566) | run 36718620936 (00a130d) |
|---|---|---|
| job total | 170 s | 137 s |
| setup (job, checkout, uv, node) | 6 s | 18 s (setup-node 11 s) |
| npm ci | 9 s | 8 s |
| Backend pytest + cov (uv sync is ~2 s) | 85 s (pytest session 78 s) | 61 s |
| Frontend Vitest | 67 s (Duration 66.35 s) | 46 s |

- Pytest, from the log of run 36715091184:
  - about 32 s pass between uv sync and the "4 workers [1290 items]" header, spent on
    collection in every worker under coverage;
  - the last ~6% of tests take about 27 s: a long tail, see the slow tests below.
- Vitest: "environment 37%, tests 33%, import 14%, setup 12%". jsdom was created 75 times, 68.65 s in total.

## Local findings (32-core VM, loaded, -n 3)
- Slowest backend unit tests (--durations):
  - 36.55 s `tests/meta/test_toolchain.py::test_ruff_format_and_mypy_clean`: runs `mypy tumnis`.
    Mypy takes **46 s cold** and **~1 s warm** (backend/.mypy_cache).
  - 28.82 s `tests/meta/test_acceptance_inventory.py::test_every_phase_acceptance_id_has_a_tagged_test`:
    runs a full `pytest --collect-only` subprocess on backend and profiles, plus node TS parsing.
  - The rest are small: 6.6 s `test_lifecycle_stateful`, 3.9 s `test_spec_guard::test_typescript_fails_to_test_flip...`,
    3.1 s `test_crypto::test_envelope_round_trip`, 2.6 s `test_timeout_fires_inside_budget`, then under 2.3 s.
- `pytest --collect-only` takes 14 s without coverage and 26.5 s with `--cov`. Branch coverage (ctrace) roughly
  doubles it. Profile of the collection time:
  - assertion rewriting: 6.6 s;
  - FastAPI route building: 5.7 s;
  - `tests/acceptance/test_a0_3_tenant_isolation.py::pytest_generate_tests`: 4.7 s.
- The semgrep meta test fails locally only (the ~/.semgrep read-only issue, known).

## Planned changes (not yet made)
1. **Cache `backend/.mypy_cache`** in the unit job with actions/cache (pinned SHA):
   - key: `mypy-${{ runner.os }}-${{ hashFiles('backend/uv.lock','backend/pyproject.toml') }}-${{ github.sha }}`;
   - restore-keys: the same key without the sha.
   - This turns the 36-46 s test into about 2 s. It's the biggest single win and likely removes
     the pytest tail. Consider the same for the lint job's mypy (optional).
2. pytest `--dist worksteal --durations=20` (the contract and integration jobs already use
   worksteal). This balances the tail and reports the slow tests in CI logs.
3. Try running Vitest concurrently with pytest: start Vitest in the background writing to a log,
   wait for it, cat the log, and fail if either failed. Measure it in CI; with 4 vCPUs the two
   runs contend, so keep this only if the timings prove it helps. Tune Vitest `--maxWorkers`
   to match.
4. Investigate `COVERAGE_CORE=sysmon`. Check the coverage docs (Context7 id
   `/coveragepy/coveragepy`, pinned coverage==7.16.2): sysmon is believed to support branch
   coverage only on Python 3.14+, and we're on 3.13, so it likely gives no gain.
5. Don't touch tests, assertions or markers. Report the slowest tests in the PR body.

## Next steps
- Do the Context7 and first-party doc checks (vm-agent-rules "Third-party docs") for:
  actions/cache; setup-uv cache; pytest-xdist `--dist worksteal`; Vitest `maxWorkers` / pool; coverage sysmon.
- Edit .github/workflows/ci.yml (unit job only), run `make check`, commit `ci(unit): ...`, and
  push. Then gather real CI step timings with `steps.py`. Iterate so the second run shows a warm
  mypy cache.
- Open the PR:
  `gh pr create --base main --head fix/ci-unit-speed --title "ci: make the unit job faster (same 3-minute budget)" --body-file $TMPDIR/ci-unit-speed/pr-body.md`
  - the body gives before/after timings and cites the docs;
  - it ends with a blank line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Then `gh pr comment <url> --body "@coderabbitai review"`, work the review loop, and send
  "#<PR> MERGE-READY at <sha>" to main.
