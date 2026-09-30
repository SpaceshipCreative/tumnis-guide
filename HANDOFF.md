# P0-29 handoff (Performance baseline)

Written on "HANDOFF NOW" from the context watcher. Branch `wp/P0-29`, draft PR **#88**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/88). Draft only so CI runs; the
body is a placeholder. CodeRabbit skipped it as a draft; no review threads yet.

## Commits (on top of main 260d18d)

| SHA | What |
| --- | --- |
| 756b919 | `test(perf): P0-29 spec tests (red)`: T-01..03 (`frontend/e2e/perf/timing.spec.ts`, `test.fail()`), T-06 (`perf/thresholds.test.mjs`), T-07 (`backend/tests/perf/test_query_counts.py`, xfail strict); e2e fixtures re-sign-in after a reset + timing helpers |
| 1764105 | merge origin/main |
| dcacaf7 | `perf/k6/limits.js` (min(NFR, 1.2 x baseline p95)), `perf/check_summary.mjs`, `perf/summary.mjs`, `perf/write_baseline.mjs`, bootstrap `perf/baseline.json` (= NFR values), `perf/package.json` (type module), Makefile: `make check` runs `perf/thresholds.test.mjs`, new `make perf-baseline` |
| 6eac027 | `perf/k6/{lib,quickadd,dashboard}.js`, `perf/sign_in.mjs` (load user sign-in, k6 API key, PROJECT_ID, LHCI cookie header), `frontend/lighthouserc.json`, `.gitignore` (.lighthouseci/), `ci.yml`: real `performance` job; e2e job gets `--grep-invert "@A0\.6|@P0-29"` |
| c72865a | `tumnis:review-ready` mark on the review route (placeholder; P1-13 must keep it) |
| 1406c70 | T-07 xfail marker removed |
| 90a4db7 | e2e fixtures: `POST /v1/test/reset` timeout 120 s (load set outlasts the 10 s action timeout) |
| 4e47523 | T-07 CEILINGS from CI counts (projects 6, board 9; seed == load everywhere, no N+1) |

## Spec tests

- T-P0-29-06 (node:test): **green** locally (`node --test perf/thresholds.test.mjs`).
- T-P0-29-07 (integration): unmarked; CI run 36684079382 showed seed == load on all five
  endpoints and failed only on two ceilings, now raised (4e47523). **Not yet re-run in CI.**
- T-P0-29-01, -02, -03 and A0.6: **still `test.fail()`**. Never verified unmarked: locally
  the VM is too loaded (a load reset takes 55 s to >120 s), and in CI the Playwright step
  never ran (LHCI failed first, and the job is over its budget anyway, see below).
- T-P0-29-04/05 (k6 thresholds) and T-09 (bundle): pass in CI. T-08 (LHCI): **fails**.

## CI state (run 36684079382 on 1406c70)

Green: lint, unit, contract, daemon, e2e, skills, spec-guard, red-proof, traceability.
- `security`: fails on Trivy Debian OpenSSL CVEs in the base image. Pre-existing on main;
  `fix/openssl-cves` owns it. Don't touch the Dockerfile.
- `integration`: failed only on T-07's ceilings (fixed in 4e47523).
- `performance`: failed at LHCI. Step times: stack + installs 59 s, bundle 1 s, load reset
  **32 s** (curl), k6 91 s (quick_add p95 29 ms, typeahead ~10 ms, dashboard ~20 ms; all
  0% failures), LHCI 102 s -> 4:44 before Playwright even starts.

## Two structural problems (decide these first; both are Scott items)

1. **5-minute budget (locked, T-P0-03-16 BUDGETS) cannot hold the plan's job on a
   GitHub-hosted runner.** Each Playwright timing test pays a seed reset plus a ~32 s load
   reset; five of them (A0.6 x2, T-01, T-02, T-03) add ~3 min. Advisor's recommended path:
   - Gate the Playwright timing step with `if: runner.environment == 'self-hosted'` (the
     `skills` job's pattern; the plan says the timings run only on the homelab runner).
   - Cut k6 to quickadd 30 s / dashboard 20 s (plan default 60 s; note the deviation).
   - Prove A0.6 once: a temporary commit that drops the e2e job's `--grep-invert` with
     the `test.fail()` markers removed (e2e budget 10 min, currently ~3:40); read the
     result, then restore. If they pass, remove the markers and ask Scott whether to keep
     them in e2e until `HOMELAB_RUNNER` is set. If they fail on timing, keep the markers
     and report the numbers. Fallback: a spec-change PR raising `BUDGETS["performance"]`
     (decision 8 precedent).
2. **LHCI fails the plan's CWV budget at Lighthouse's default simulated mobile throttling**
   (slow 4G, 4x CPU): LCP 5.7 s (dashboard) / 7.3 s (board), TBT 385 / 301 ms; CLS passed.
   Next: read the LHR JSON (`.lighthouseci/` in the `performance` artifact; the first run's
   artifact only had k6 summaries because upload paths under `frontend/` were empty at
   failure time: check the upload paths) for `largest-contentful-paint-element`, critical
   chains, `bootup-time`. Then one CI run with a LAN/VPN network profile and phone CPU
   (`collect.settings.throttling` rttMs 50 etc.; verify Lighthouse 12.6 field names in
   Context7). If TBT still fails, set the three assertions to `warn` (plan's cut line: LHCI
   may move to phase 4), keep the numbers in the log, and put the choice to Scott. Do not
   leave the job red, and do not loosen silently.

## Remaining steps

1. Push this branch (includes 4e47523); confirm `integration` goes green (T-07).
2. Apply the job changes above (gate Playwright, k6 durations, LHCI decision); re-run CI
   and check the `performance` job fits in 5 min.
3. Baseline: download the `performance` artifact (`k6/*.json`) from >= 3 green CI runs and
   run `make perf-baseline SUMMARIES="run1/*.json run2/*.json run3/*.json"
   RUNNER=github-hosted-ubuntu-latest COMMIT=<sha>`; commit `perf/baseline.json` (the PR
   needs the `perf-baseline` label per the plan; flag for the coordinator).
4. A0.6 proof run as above; remove markers only on a green run (approved TDD step).
5. `make check` (vitest is flaky under VM load; #79 merged on main), backend layers with
   `-n 3`, then mark the PR ready, write the real body (see notes below), comment
   `@coderabbitai review`, and run the review loop.

## Decisions and deviations so far

- LHCI signs in with `collect.settings.extraHeaders` (session cookie from
  `perf/sign_in.mjs`) instead of the plan's Puppeteer `frontend/e2e/lhci-login.cjs`: the
  LHCI docs say `puppeteerScript` needs `puppeteer` installed (a large new dependency and
  browser download); `extraHeaders` is the documented alternative
  (github.com/GoogleChrome/lighthouse-ci/blob/main/docs/configuration.md). Board URL uses
  the runtime project id (the plan's fixed uuid does not exist in the load set).
- k6 via the `grafana/k6:2.3.0` image pinned by digest; `--summary-export` still exists in
  k6 2.x (checked `k6 run --help`); `crypto.randomUUID()` instead of the jslib remote import.
- Typeahead query `q=lo` (load set project names), not `q=ac`.
- T-07 is one test looping over the five endpoints (one load fixture instead of five);
  counts over HTTP include the session check, so ceilings are measured, not the plan's
  "projects list 3".
- `perf/k6/limits.js` is a plain shared module so node:test can import the rule; `lib.js`
  keeps `open()` for k6 only.
- e2e fixtures: `seededApp.reset(set)` signs a signed-in browser in again as that set's
  user (the load set has its own user, `load@example.test`); helpers `seedSetUser`,
  `coldPage` (CDP 50 ms latency), `quickAddUsable`, `median`, `serviceWorkerActive`.
- `tumnis:review-ready` lives on the placeholder review route; DS-01 and P1-13 must keep
  it and the 200 KB budget.

## Scott items

- Performance budget: 5 min cannot hold k6 + LHCI + five load-set timings on GitHub-hosted
  runners (numbers above). Options: timings homelab-only (plan) with a one-off proof, or a
  spec-change raising the budget.
- CWV budget fails on main at default mobile throttling (numbers above): throttling
  profile for a LAN/VPN app, or warn-only until phase 4.
- 20% rule on GitHub runners: quick_add p95 is ~29 ms, so 20% is ~6 ms of runner noise;
  baseline is max-of-N CI runs, but it will still flake until the homelab runner exists.

## Verify commands

- `node --test perf/thresholds.test.mjs scripts/ci/check_bundle.test.mjs`
- `gh pr checks 88`; performance job log: `gh api --allow-escape-sequences
  repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs`
- Local Playwright (bare docker, stack from `make up`): `docker run --rm --network host
  --ipc host -e E2E_BASE_URL=http://localhost:8080 -v <worktree>:/w -w /w/frontend
  mcr.microsoft.com/playwright:v1.63.0-noble npx playwright test --grep "@P0-29|@A0\.6"`
  (timings are meaningless on this VM; use it only for functional errors).
- The local compose.test stack was stopped (`make down`); `make up` rebuilds it.
