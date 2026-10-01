# HANDOFF · P2-05 Questions and approvals (continuation 1 → 2)

PR #121 (https://github.com/SpaceshipCreative/tumnis-guide/pull/121), branch `wp/P2-05`, base main. `@coderabbitai review` requested once at open (don't request another full review). The PR body (summary, deviations, docs, Scott item) is in `$TMPDIR/P2-05-c1/pr-body.md`. Edit it with `gh pr edit 121 --body-file` when markers or results change.

## Commits

- `1545174` test(agents): P2-05 spec tests (red), from c0.
- `738cad0` feat(agents): the approval_need rule plus defaults. T-08/T-09 markers removed (unit, green).
- `d5edcda` feat(agents): everything else in PR1's backend scope. `make check` green, unit 1588 passed. Pushed. CI not read yet.

## What's built (see the PR body for detail)

- `agents/human.py`: the api side. Models, `configure_human_waits`, `ask_human`, `request_approval`, `check_action`, `open_approval_in`, `long_poll_decision` (re-reads the row every 0.5 s after the commit), `policy_snapshot`. Re-exported from `agents/api.py`.
- `agents/human_flows.py`: the worker side. `question_flow`, `approval_flow`, steps `load_decision`, `evaluate_approval`, `close_human_wait`, plus `start_human_wait` and `deliver_human_decision` (version-aware).
- `agents/workflows.py`: `supervise_run`, `load_supervision`, `stale_workflow`, `_replace_supervisor`. `deliver_signal` replaces an older-version run workflow. `_supervise(..., waiting=)`. `HUMAN_QUEUE`.
- `agents/events.py`: subscribers `agents.start_question_flow` and `agents.start_approval_flow`. `apply_review_decision` extended for question and approval.
- `agents/review_kinds.py`: kinds `question` and `approval`, with `on_decide` hooks (row update, audit, 422 `reason_required` / `invalid_answer`).
- `tasks/review.py`: `ReviewKindSpec.on_decide`, `Deciding`. `_decide` takes `actor`. `human.decided.reason` comes from the hook.
- `core/agent_surface.py`: `SurfaceOp.after_commit`, `rest_twin_detached`, `PENDING_TOOLS` trimmed.
- `decisions`: `questions/approval_need.py`, `api.ask_approval_need`, and a cache-key generation bumped by `use_providers` (T-10 would otherwise read case 1's cached answer).
- Migration `agents_0006`, events + fixtures + `make gen`, worker `human` queue, test scaffolding (SAMPLES, meta/acceptance autouse fixtures, `_runs.py` teardown).

## Next steps

1. `gh pr checks 121`. Integration/acceptance tests still carry strict xfail `spec:P2-05`. An XPASS(strict) failure in CI means the test passed: remove that marker (Scott-approved step). A real failure: read `gh run view <id> --log-failed` and fix. Local `make test-int` at most once per continuation (Docker load rule).
   - Integration tests: `agents/tests/integration/test_questions.py` (T-01, -02, -14), `test_approvals.py` (T-03, -04, -05, -10, -11, -12, -13), `test_approval_deploy.py` (T-06, -07).
   - Acceptance A2.2 and A2.3: `tests/acceptance/test_a2_2_question_resumes_run.py`, `test_a2_3_approvals_and_taint.py`. Remove their markers only if they genuinely pass.
   - Watch the P2-01 meta sweeps (`tests/meta/test_mcp_*`, `test_taint_sweep.py`) with the two new ops. T-P2-01-09 only covers update ops; T-08 needs `idempotency_key_required` on both doors (the detached twin passes None → 400).
2. Likely trouble spots: event ordering of `run.signal{waiting}` vs `{resumed}`; `park_task` when the task isn't `in_progress`; T-06 relies on `stale_workflow` and `_replace_supervisor` (the v2 worker cancels v1's `dispatch_run` and starts `supervise:<run>:v2`); T-14 recovery on the same version.
3. CodeRabbit loop per pr-review-loop.md. When CI is green and there are 0 open threads, SendMessage to main: "#121 MERGE-READY at <sha>".
4. PR2 on `wp/P2-05-impl-2` (from wp/P2-05): Vitest T-15/T-16 red first, then `QuestionItem.tsx`, `ApprovalItem.tsx` (reason presets, blank blocked), review slots, `PolicyEditor.tsx` + `PUT /v1/projects/{id}/policy` (versioned, 409 current, a class in both lists is 422, `policy.changed` + audit), `make gen`. e2e `A2.2-question.spec.ts` stays `test.fail()`.
5. Delete HANDOFF.md in a chore commit when done.

## Deviations (also in the PR body)

1. Keyless call answers 200 `denied` / `run_token_required`.
2. `HumanWaitOut.tainted`.
3. The long poll reads the row, not DBOS events.
4. REST twins `idempotent=False` (idempotent inside invoke; long poll after commit).
5. Known verdicts open in the request transaction.
6. Deterministic workflow ids (DBOS 3.1.0: no dedup on partitioned queues).
7. Plan class names in `DEFAULT_GATED` vs the projects stored default vocabulary.
8. `settings.human_wait_poll_seconds` not wired (`configure_human_waits` is the knob).
9. No `audit_cases.py` cases for the approval actions.
10. The decisions fake is loaded via importlib in `_human.py`.
11. A shared decisions cache-key change.

## Verify

```
SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-05-c1/semgrep.yml SEMGREP_LOG_FILE=/tmp/claude-1002/P2-05-c1/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-05-c1/semgrep.ver PYTEST_XDIST_AUTO_NUM_WORKERS=3 make check
make test-int   # bare, once per continuation; CI is the authority
```

Bash heredocs near paths are refused by the worktree guard: write helper scripts with the Write tool into `$TMPDIR/P2-05-c2/` and run them with `uv run python <file>`. Frontend `node_modules` is installed in this worktree.

Context7: `/dbos-inc/dbos-docs` (application versions, `cancel_workflow_async`, `get_workflow_status_async`), cited in the PR body.

## Scott items

- T-P2-05-13 vs the locked P2-01 sweeps: keyless gated calls answer 200 `denied` / `run_token_required` instead of 403.
