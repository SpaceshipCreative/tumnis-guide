# P4-01 (Guardrail level): handoff c2

Branch: `wp/P4-01` (push with `/usr/bin/git push origin HEAD:wp/P4-01`). **No PR yet.**
Instructions: ~/tumnis-coordinator/prompts/wave1/P4-01.txt (read it whole, including the restart notes) and the
coordinator notes in agent-reports/P4-01-coordinator-notes.md.

## Done (all implementation; every spec marker removed after its test passed)

- `7be3ae8` feat(focus): guardrail rules (T-01, T-02, T-03 unmarked; rules.py 100% line and branch coverage).
- `b0bbd38` feat(focus): focus_0002 migration (down focus_0001, expand; detour_task_id, return_to_task_id,
  return_decision CHECK NOT VALID, decided_at), detour capture on respond, POST /v1/focus/return,
  `guardrail` + `detour` on GET /current, focus.prepare_next subscriber, agents.api
  `register_enrichment_starter` / `enrich_ahead`, end_sessions also returns sessions ended at `at`.
  Generated outputs committed (`make gen`). FocusCurrentOut uses json_schema_serialization_defaults_required.
- `a372609` feat(frontend): prefetch (T-10 unmarked). It uses `queryClient.query` because `prefetchQuery` is deprecated in 5.104.
- `756b124` feat(frontend): focusSession detour/returnPrompt states (T-11 unmarked).
- `55d7805` feat(frontend): GuardrailDashboard, levelRules, SwitchPicker, ReturnPrompt, FocusBar guardrail
  controls, loader reads the focus level (T-09 and T-13 unmarked). Also adds my own test `FocusBarDetour.test.tsx`.
- `b7f4733` test(focus): unmarked T-04..T-08. All six ran as XPASS(strict) in the local `make test-int` (my one run).
- `87343fb` removed the old HANDOFF.md (this file replaces it).
- Local results: `make check` green (backend unit 1767 passed, Vitest 310 passed, lint and typecheck clean).
  `make test-int`: the six P4-01 integration tests passed. The other failures were environmental: minio Docker 500s
  in test_storage_s3 and harness[minio], typeahead latency 1.26 s under load, test_notify_wakes_relay, the known
  rclone timeout, and test_seed_project_survives_round_trip ("deliveries still running after 30 s",
  most likely load). Watch that last one in CI: focus.prepare_next adds one delivery per task.status_changed.

## Remaining steps

1. `/usr/bin/git fetch origin; /usr/bin/git merge origin/main`. main moved: #134 P2-12, #136 decisions 54-55, #132 P2-17.
   If openapi/frontend/src/api conflict, regenerate with `make gen` (needs `npm ci --prefix frontend` in a new worktree)
   and don't hand-merge. Check `cd backend && uv run alembic heads`: focus head must be focus_0002 only.
   Run `make check` (semgrep env vars below), then push.
2. Open the PR. The draft body is at `$TMPDIR/P4-01-c1/pr-body.md` (/tmp/claude-1002/P4-01-c1/pr-body.md). Copy it
   into your own subfolder and fill RESULTS_PLACEHOLDER with the per-layer results. Also fix the wording
   "two task_changes rows" to "two undo-history change rows (record_change)". Then:
   `gh pr create --base main --head wp/P4-01 --title "[P4-01] impl: guardrail level" --body-file <file>`
   and `gh pr comment <url> --body "@coderabbitai review"` (once).
3. Run the review loop (~/tumnis-coordinator/pr-review-loop.md) until CI is green and CodeRabbit has no open threads,
   then SendMessage main "#<PR> MERGE-READY at <sha>". `preview` stays pending (expected).

## Deviations (also in the PR body draft)

- T-P4-01-12 and the master focus SKILL.md are deferred to P2-16 (P2-16 follow-ups). The fixed template is
  "Captured. Back to <task>?" with rule "Guardrail · detour".
- A4.1 is not in this PR (it isn't on main, and its seed data belongs to SEED).
- The previous task goes in_progress -> backlog -> today in one transaction (RESET then PLAN edges). tasks/rules.py
  and tasks/api.py are unchanged.
- The agents.api enrichment-starter seam (approved). The prefetch covers the task plus its packet (approved).
- The stored rule text is "Guardrail · detour", not "guardrail.detour".
- prepare_next is a subscriber in focus/events.py (logic in focus/api.py), not in workflows.py.
- The focus bar has Switched (opens the picker) and a standing Less of this at Guardrail. The card has no Switched/Stuck
  buttons and the w/!/Enter keyboard shortcuts are not built.

## Scott items

- The intermediate Backlog step in detour capture produces two task.status_changed events. That means two digest
  `task_changed` entries (In progress->Backlog, Backlog->Today) and two undo-history change rows. Focus is unaffected.
  Options: (a) keep it; (b) a follow-up adds a tasks.api in_progress->today path with one event, which needs a new
  edge in the locked transition table or a special-cased emit.

## Verify

```
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/focus
cd frontend && npx vitest run --maxWorkers=4 src/machines src/lib src/components/dashboard src/components/focus
SEMGREP_SETTINGS_FILE=$TMPDIR/<you>/semgrep/settings.yml SEMGREP_LOG_FILE=$TMPDIR/<you>/semgrep/log SEMGREP_VERSION_CACHE_PATH=$TMPDIR/<you>/semgrep/vc make check
```
Rely on CI for integration (DOCKER LOAD RULE).
