# HANDOFF: JOURNEYS-C (fix/acceptance-knowledge-ops, PR #163)

The binding prompt is ~/tumnis-coordinator/prompts/wave1/JOURNEYS-C.txt. PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/163 (DRAFT; CodeRabbit not requested yet).

## Commits (on top of main 67a43da6)

- 10411cc7 `feat(knowledge): acceptance seed gets a default storage location (APP-06)`: seed `locations` section, knowledge `seed_location` writer, backend/fixtures/acceptance/locations.yaml, tests/harness/test_acceptance_seed_location.py
- cc002e18 `feat(knowledge): drop a file on the composer; Knowledge shows its progress (APP-07, A1.5)`: Composer drop target, useKnowledgeUpload, file status labels and polling, RailSection regions, "Packet preview" button, packet parts as articles, ComposerDrop.test.tsx, PacketPreviewParts.test.tsx
- 76cb99e0 `fix(knowledge): status label on every file item, whatever its type (A1.5)`, not yet seen by CI when this was written. The server's `kind` for an upload is the file type (`pdf`), not `file`, so the label showed only on the optimistic row. Found by the local e2e run.

## Journey status

- **A1.5 Playwright**: a local diagnostic run (decision 76, an untracked copy without test.fail(), deleted afterwards) against the compose.test stack built from this branch passes every step up to the citation, on phone and laptop: drop, item count +1, Scanning, Ready, Packet preview, brief article first. It fails ONLY at `parts.filter({ hasText: "Rate card, page 2" })`, because the passage is cited `rate-card-table.pdf, page 2`; its text holds "Senior designer | 160". This needs a DECISION (sent to main): (a) a spec change to the expected citation (recommended), (b) or (c) a title rule, though neither matches the case-sensitive check. The marker stays.
- **A1.5 integration**: 3 xfails. No product defect: with FakeDocling, which holds real Docling output, all 4 pass locally (diagnostic copy, deleted). The real pipeline fails with ModuleNotFoundError: docling. Scott item: Docling in CI (details in the P1-16 impl-2 notes, and in the PR body copy at /tmp/claude-1002/JOURNEYS-C/pr-body.md: job placement, the typer<0.27 pin conflict, the torch CPU index, egress to HF and www.modelscope.cn, a model cache of about 570 MB). Markers stay. Do NOT fake the extractor in tests/acceptance/_phase1.py.
- **A0.4**: drill-only (`pytest -m drill`, restore-drill.yml, real pgBackRest restore). Homelab/Scott item.
- **A0.5**: no markers left; green in CI.

## CI state (at the handoff)

- On the second commit (before the isFile fix), 13 checks passed, 3 were pending and 1 failed: `e2e` (run 36964275886, job 110704495841). Only A1.5 failed, on phone and on laptop, at line 41 `toContainText("Ready")`. The item showed "Scanning" and then no label at all, because the server's kind is `pdf`. The run hit the 30 s test timeout, and under `test.fail()` a `timedOut` result does not count as the expected `failed`, so the job went red. The other ✘ lines (J1/J2/J3/J6/J7/J8) are expected failures already marked on main.
- The isFile fix (76cb99e0) should turn e2e green: A1.5 then reaches the citation step and fails there with an assertion, which `test.fail()` counts as expected. FIRST STEP for the successor: confirm that with `gh pr checks 163 -R SpaceshipCreative/tumnis-guide`. If A1.5 still times out, the run takes longer than the 30 s test timeout and needs a faster settle (poll interval, worker pickup). Do not change the spec's timeout.

## Messages

- Sent to main: the decision items (citation, Docling, A0.4, A0.5 status, and the product choice that a drop opens the Knowledge section, which is the Context sheet on phone). No reply yet.

## Remaining steps

1. Wait for CI on #163 (`gh pr checks 163`). Fix real failures. A cancelled job may be re-run once.
2. Wait for main's answer on the citation. If a spec change is approved, the coordinator handles it with the spec-change label; then remove A1.5.spec.ts's `test.fail()` only after CI shows "expected to fail, but passed", citing the run id (decision 78).
3. Mark ready (`gh pr ready 163`), comment `@coderabbitai review` once, and run the review loop (pr-review-loop.md).
4. When CI is green and there are no open threads, send main "#163 MERGE-READY at <sha>". Expect it to be READY EXCEPT MARKERS: A1.5 (citation decision) and the A1.5 integration markers (Docling).

## Verify

- `make check` (set SEMGREP_SETTINGS_FILE, SEMGREP_LOG_FILE and SEMGREP_VERSION_CACHE_PATH to files under /tmp/claude-1002/JOURNEYS-C/)
- `cd frontend && npx vitest run src/components/project`
- `cd backend && uv run pytest -n 0 tests/harness/test_acceptance_seed_location.py tests/harness/test_acceptance_seed.py tests/harness/test_acceptance_seed_writers.py` (needs Docker, so run it outside the sandbox)
- Local e2e: `docker build -f deploy/Dockerfile -t tumnis:jc .`; `TUMNIS_IMAGE=tumnis:jc TUMNIS_TEST_PORT=18985 docker compose -p tumnis-jc -f deploy/compose.test.yaml up -d --wait`; `E2E_BASE_URL=http://localhost:18985 npx playwright test <spec>` (use localhost, not 127.0.0.1, or the session cookie is lost). Then `down -v`.

## Shared-file edits

backend/tumnis/seed.py (additive), frontend/src/stores/uiStore.ts (additive).
