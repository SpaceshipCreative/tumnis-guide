# Owner actions

What the build still needs from the project owner: settings only the owner can change, and decisions the build agents left open. Last updated 2026-10-03, on main at c6a69b3f.

Build status: 79 of 86 work packages are merged. The other 7 are the Phase 3 connectors deferred on purpose. The final application test's findings are fixed, except APP-F05 and APP-F06 below. #186 moved frontend Vitest into its own `unit-frontend` CI job.

## 1. Do these (only you can)

- [ ] **Make `unit-frontend` a required check** (added by #186). It's a branch protection or ruleset setting. `scripts/ci/branch_protection.sh` applies `.github/required-checks.txt`, which #186 updated.
- [ ] **Decide whether `docling` becomes a required check.** It now builds the extract image and runs real extraction end to end on the production compose stack, in about 7 minutes. It isn't required today. Making it required is a spec change to the locked CI budget table.
- [ ] **Allow build-time network access for the extract image** (#185). Building `worker-extract`'s image downloads from Hugging Face, ModelScope and the PyTorch CPU index. At runtime it needs none of them (`HF_HUB_OFFLINE=1`). The image is about 4.7 GB; the slim image for the api and the other workers is unchanged.
- [ ] **Deploy host:** Docker Compose 2.24.0 or later. Coolify needs a route for the gitignored `deploy/hosted-keys.env` (a file mount or an absolute host path).
- [ ] **Homelab runner:** it's offline, so the `preview` check stays queued on every PR. The P4-04 done item "a real overnight run on the homelab lands in the morning review" also needs it.

## 2. Decide before the first release

- [ ] **Offsite backups (B2, repo2) never expire.** pgBackRest can't delete in repo2 (its key has no delete permission), so the "delete 35 days after hidden" lifecycle rule never fires. The UI text (PurgeDialog, RetentionSection) promises purged content leaves backups after 35 days, which isn't true for repo2. Options: (a) fix the lifecycle design (for example, exclude `*.info`, or give pgBackRest a separate delete key); (b) change the UI and docs promise; (c) both.
- [ ] **Release checklist.** Rows 5, 6, 7 and 13 can only get evidence after tagging, but on a `release/*` branch the check requires a link in every row. Options: (a) spec change: accept `post-tag:` rows on `release/*` and require every link on the tag build; (b) process: tag from the `release/*` head while its CI is red, then commit the links; (c) leave as is.
- [ ] **Postgres connection budget.** Worst-case pools add up to 155 (api 42, worker 61, worker-extract 52) against `max_connections=100`. Pools grow lazily and the limit has never been hit. Options: (a) shrink the DBOS pools, which cuts workflow capacity; (b) raise `max_connections`, for example to 200; (c) leave it. The current default is (c).

## 3. Product and UX decisions

- [ ] **APP-F05:** `GET /v1/plan/{today}` answers 404 until the planner publishes a plan, which shows as a console error on the dashboard. Options: (a) return 200 with an empty plan (a contract change); (b) the client treats 404 as "no plan yet"; (c) leave it.
- [ ] **Rate limit (#176):** is the per-user limit (burst 50, then 10/s) enough? J1 sends about 84 requests in 3.4 s. Every plan change triggers a duplicate refetch. When the plan read fails with anything other than 404, the Today panel quietly falls back to the Today tasks instead of saying the plan couldn't load.
- [ ] **Settings screens (APP-13):** GitHub, Coolify, planning, focus and triage settings can only be changed through the API. The plan specifies no screens for them.
- [ ] **Plan issues (APP-12):** Edit on a plan issue still opens the generic "Value" form.
- [ ] **Recurrence (APP-09):** add `recurrence_rule_id` to the task API so older instances show their rule.
- [ ] **Delete-at-source (#154):** the dialog exists, but no screen opens it.
- [ ] **Files:** vault attachments have no storage location yet, so they can't be downloaded, and S3 documents have no original-file download.
- [ ] **Attachment re-extraction:** a lost extraction is requested again only after 1 hour, so that "a second unchanged sync requests nothing" (T-P3-12-07) holds. Pick another interval, or change the spec to re-request on every scan.
- [ ] **A1.5 step 4:** Docling's real chunk heading path for a table is `["Rates"]`, without the document title, so a passage query that names the document misses it. Options: (a) index chunks with the document title; (b) a spec change; (c) make FakeDocling match the real output.
- [ ] **Synced documents are read-only:** trash, edit and DELETE answer 409 `read_only_source` for every connection-synced document, S3 included, not only vault ones. Confirm this is wanted.
- [ ] **Dashboard:** the plan-issues list (`FitOfferList`) in the laptop left column has no height limit. With several issues the page scrolls; Today keeps its 16rem minimum.

## 4. Security choices to confirm

- [ ] `tumnis mcp-stdio` sends the API key over plain `http://` to LAN addresses. Options: refuse anything but https outside localhost, or warn. The README already recommends https.
- [ ] The OAuth client allows plain http for the token and registration endpoints on LAN addresses (decision 7). The MCP spec and SEC-9 say https everywhere. Keep the LAN exception, or go https-only?
- [ ] The MinIO bearer token stands in for a webhook signature (P3-13). Acknowledge it, or require signed events.
- [ ] Git host keys are confirmed on first connect and then pinned strictly; a pasted `known_hosts` line is also accepted (decision 82).

## 5. Tests and CI

- [ ] **APP-F06 (J1 A1.2 flake):** the journey reads the task before the accept request commits. The app is correct. The fix changes a journey step in `J1.spec.ts:86-96` (wait for the response) and no assertion, but spec tests are locked, so it needs your OK.
- [ ] **Performance budget:** a median of 5 Lighthouse runs doesn't fit the 5-minute job budget. Raise the budget (spec change), or keep 3 runs and accept occasional dashboard TBT noise (50-220 ms against a 200 ms budget).
- [ ] **Locked deadlock tests:** after #183, the docstrings of `test_reset_deadlock_worker_write.py` and `test_reset_deadlock_attempts.py` describe the old lock order. Docstring only, but the files are locked.
- [ ] **Red-proof:** `test_s3_source_worker` (#182) and P3-13's LinkedObjectChangedError test were never shown failing in CI against the old code. Red-proof needs a `bug`-labelled issue and a `test_issue_<n>_` name.
- [ ] **T-P0-07-05** (relay kill/restart drain) fails about 5 times in 200 and isn't root-caused yet. #171 added the instrumentation for the next failure.
- [ ] **Review gaps:** 7ad85cbf (the vault-row FOR UPDATE lock) merged without a CodeRabbit pass. #158 (P3-13) lists 11 deviations in its PR body to review.

## 6. Coordinator defaults you can override

Each of these was decided as a default while waiting on you. They're recorded and reversible.

- 86: in fakes mode the worker follows the test clock.
- 87: an OAuth callback with neither a code nor an error answers 404.
- 89: S3 delete-at-source is refused (409 `linked_source_read_only`).
- 91: knowledge `move.fail` only changes a move that is still copying.
- 92: a finished OAuth connect deletes its pending row.
- 93: websocket refetches are coalesced (first one immediate, then a 200 ms window).
- 94: the performance job takes a median of 5 Lighthouse runs, with budgets unchanged (see section 5).
- 95: a stuck `syncing` connection is recovered as a lease.
- Agent runs: `started_at` is wall-clock time while the fakes stack runs a test clock. It is also the basis for the wall-clock ceiling, so either give the ceiling its own start time or move both onto the worker clock.

## 7. Nice to have

- Extract image trims: `opencv-python-headless` (about 216 MB less) and dropping an unused layout ONNX model (about 164 MB less).
- Long PDFs take about 6.5 minutes for 210 pages (peak memory 2.4 GiB, within the 4 GiB limit).
- `/health/ready` reports `backups: ok` while backups are off.
- The 409 `no_location` detail is misleading when the location's marker is missing.
- The Obsidian "first sync has started" status never shows.
- Phone project views have no tablist.
- ADR-0014 (Discord as the first chat provider) is still to be written.
- Check the Git write probe's wording rules against the homelab Git host.
- Hosted mode offers the Git source only.
- The `tumnis seed` CLI cache has no invalidation publisher.
