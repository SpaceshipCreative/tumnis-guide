# HANDOFF · P1-08 Enrichment by the project agent (c2 → c3)

Stopped on "HANDOFF NOW" from the context watcher. Branch `wp/P1-08`, **PR #109**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/109), open, body up to date (a copy is
at `/tmp/claude-1002/P1-08-c2/pr-body.md`; the next agent uses `$TMPDIR/P1-08-c3/`).

## Commits added in c2 (on top of bf4414f)

- `f7fae8a` merge of wp/P1-08 into the c2 worktree (main was cf20116)
- `b8250de` fix(agents): no enrichment workflow for a project without an agent; NOT VALID checks
- `9f52427` perf(agents): enrichment subscribers direct; literal SQL in tasks_0007 (semgrep)
- `36d636f` fix(agents): review of #109 (failed status on a raised step; AI label drops the
  estimate; revised label closes the `low_confidence_label` item; drawer criteria order and
  Enriched Undo change id) + tests `tasks/tests/integration/test_apply_enrichment.py`,
  `frontend/.../TaskDrawer.enriched.test.tsx`
- `c229538` chore: remove P1-08 handoff notes
- `55827cc` merge main (#105 P2-18, #108 P2-00 spec); conflict in agents/workflows.py kept both
- `eccd527` perf(agents): remember a project without an agent (`agents.events.project_has_agent`,
  `NO_AGENT_TTL` 5 s, `NO_AGENT_MAX` 1024) + unit test `agents/tests/unit/test_no_agent_cache.py`;
  Enriched Undo for any finished enrichment (CodeRabbit outside-diff comment) + Vitest case
- `6b9aec4` merge main (#110 P2-11), clean
- this commit: HANDOFF.md

## CodeRabbit

- Review 1 (f7fae8a): 6 inline threads, all fixed or answered and resolved (CodeRabbit agreed in
  each thread; one partly declined: no `_estimate(..., AGENT)` for Human/Hybrid, CodeRabbit
  withdrew it).
- Review at 55827cc: 1 outside-diff comment (Undo for criteria-only enrichment): fixed in
  eccd527, answered in a top-level PR comment (no thread to resolve).
- Review of 6b9aec4: CodeRabbit check `pass`; check for new comments (inline + review body
  "outside diff"/"nitpick") before MERGE-READY. Auto incremental reviews are on; do not
  request a full review.

## CI state at 6b9aec4 (when stopped)

All green except `integration` (still running) and `preview` (pending by design, no homelab
runner). performance passed (was failing: k6 quick_add p95 359/261 ms before the direct
subscribers), contract (squawk) and security (semgrep) pass.

**The open question is `integration`**: T-P1-07-01
`decisions/tests/integration/test_label_latency.py::test_label_p95_under_1s_at_recorded_latency`
(serial part) failed at f7fae8a, b8250de and 55827cc (fastest of 40 at 626-702 ms vs a 250 ms
Jev fake). Diagnosis: the relay runs direct subscribers serially and `agents.enrich_on_create`
sorts before `decisions.label_on_create`; each of our handlers opened a connection per event
(NullPool in tests). eccd527 (the no-agent memory) is the fix; 6b9aec4 is its first CI run.

## Remaining steps

1. `gh pr checks 109`; for a failure `gh api repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs --allow-escape-sequences`
   (needs `allowed_domains: ["*.blob.core.windows.net"]`).
2. If T-P1-07-01 still fails: do NOT try another subscriber variant blind. Ask the coordinator
   for one sanctioned local serial run (or a throwaway CI commit running only that test) to see
   all 40 latencies. Never edit that test (P1-07 spec).
3. If `contract` shows cancelled: `gh run rerun <run-id> --failed` once.
4. Read any new CodeRabbit comments; fix or answer and resolve (`/tmp/claude-1002/P1-08-c2/threads.sh`
   lists threads; `reply.py` there shows the reply+resolve calls).
5. When CI is green (preview excepted) and no open threads: SendMessage to main
   "#109 MERGE-READY at <sha>". Then delete this file in a `chore: remove P1-08 handoff notes`
   commit (push, and re-check CI stays green; a HANDOFF.md-only change needs no make check
   beyond the usual).
6. Coordinator rules: `make test-int` locally at most once per continuation (CI is the
   authority); P2-04 also adds `tasks_0007`; whichever merges second re-chains to `tasks_0008`
   (P2-04 not merged at 6b9aec4).

## Deviations (all in the PR body, 1-13)

1-8 from c1 (workflow id instead of dedup id; status writes share statements; placeholder
folded into set_enrichment_status; apply_enrichment takes version; not-provisioned projects
skipped (5, rewritten); TaskOut str|None; enrich_on_update only after an ended enrichment;
enrichment_request in packet_builder). New in c2: 9 direct subscribers; 10 NOT VALID checks;
11 review fixes; 12 no-agent memory (5 s); 13 Undo for any finished enrichment.

## Scott items (in the PR body)

1. Hybrid split placement (`AI part:` / `Your part:` lines after the criteria; no description column).
2. A1.1 and A1.4 step 2 keep their `spec:P1-08` markers (seed lacks the projects/profiles; A1.1
   needs a REST-scriptable fake runner). Never remove them.
3. No backfill: tasks created while a project's agent is not provisioned, or up to 5 s after
   provisioning completes, are never enriched. Confirm or ask for a backfill.
4. Kill test 20x in a row and a real homelab task not provable here.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents tumnis/modules/tasks
bash /tmp/claude-1002/P1-08-c2/check.sh     # make check with semgrep paths under $TMPDIR (copy it to your folder)
npm --prefix frontend run test -- --run src/components/project/drawer/ src/components/common/FirstAction.test.tsx
python3 scripts/ci/spec_guard.py --base origin/main --head HEAD --labels ""
cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main
```
