# HANDOFF · P2-05 Questions and approvals (continuation 2 → 3)

PR #121 (https://github.com/SpaceshipCreative/tumnis-guide/pull/121), branch `wp/P2-05`, base main. `@coderabbitai review` was requested once, at open. Don't request another full review unless needed. The PR body is current in `/tmp/claude-1002/P2-05-c2/pr-body.md`. Copy it to your own folder and edit it with `gh pr edit 121 --body-file`.

## Branch setup (c3)

Throwaway worktree at main: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P2-05`. Push with `/usr/bin/git push origin HEAD:wp/P2-05`. Backend: `cd backend && uv sync --frozen --all-extras`. Frontend: `npm ci --prefix frontend`.

## Commits this continuation (on top of c1's a5ce425)

- `fb3948d`: merge of wp/P2-05 into main.
- `56ca7b9` test(agents): unmarks T-01..05, -07, -12.
- `5008b1b` fix(agents): one action-class vocabulary (P0-17 stored names) and `test_default_policy.py`.
- `7a75e6e` test(core): audit cases `approval.granted` / `approval.denied`; meta `surface_app` rate bucket.
- `e869e88` fix(agents): CodeRabbit fixes (`deliver_signal` row lock across supervisor replacement; `open_waits_query` counts only waits with a review item).
- `a1c7935` test(agents): helpers (`mcp_running` swap, `_phase2.tool` idempotency_key, `_human.started` makes room, `decide` current version and fresh sessions, `audit_rows` text ids).
- `96f0903` test(agents): unmarks A2.2, A2.3, T-10, -13, -14; `decide` uses the app's owner DSN and waits up to 3 s for the ranking.
- `8290ef0` test(agents): unmarks T-11.
- `8a4f376` chore: removes the old HANDOFF.md.
- This commit: the new HANDOFF.md. **Delete it in a chore commit before MERGE-READY.** CodeRabbit flagged the last one, and that thread is resolved.

## State

- Every P2-05 spec test is green in CI and unmarked, except **T-P2-05-06** (`test_approval_deploy.py::test_approval_survives_deploy_to_new_version`). It is still strict xfail.
  - The coordinator approved fixing it only with a targeted sandboxed run (`uv run pytest --runxfail -n 0 -p no:randomly <node>`). That run was refused (Docker socket, Operation not permitted). The coordinator said: don't work around it, list T-06 as a deviation. Done: deviation 11 in the PR body.
  - Its last seen local failure was a 401 `session_expired`, now fixed in `_human.decide`. It may pass now. If CI shows XPASS(strict) for it, remove its marker in a commit.
- CI at 8290ef0: integration was green with only T-11 XPASS, which is now unmarked. Push 8a4f376 and later CI isn't read yet. Check `gh pr checks 121`.
- `security` fails on a base-image CVE (libpcre2-8-0 CVE-2026-103111) on every PR. The coordinator said it's not ours: fix/trivy-pcre2 owns it, and they'll say when it merges. Then merge main and re-run CI.
- CodeRabbit: all 3 threads resolved (2 fixed in e869e88, HANDOFF removed). Check for new incremental-review comments: `bash /tmp/claude-1002/P2-05-c2/threads.sh 121` lists threads, and `reply.sh <pr> <comment_id> <body_file> [thread_id]` replies and resolves.

## Next steps

1. Delete HANDOFF.md (chore commit), push, and read CI. If T-06 is XPASS, unmark it in a commit.
2. When CI is green (security excepted until fix/trivy-pcre2 merges) and there are 0 open threads, SendMessage to main: "#121 MERGE-READY at <sha>".
3. Don't commit PR2 work on this HEAD until #121 is MERGE-READY (HEAD can only be pushed to one branch). Then build PR2 on top and push `HEAD:wp/P2-05-impl-2`. PR title: "[P2-05] impl-2: Questions and approvals UI and policy editor".
   - Spec first, red: Vitest T-P2-05-15 `frontend/src/components/project/PolicyEditor.test.tsx::[P2-05][FR-5.6] toggles gated and allowed and saves with version` (PUT with version; 409 shows current) and T-P2-05-16 `frontend/src/components/review/ApprovalItem.test.tsx::[P2-05][SEC-3] deny needs a reason` (presets fill the reason; blank blocked), as `test.fails`.
   - Backend: there are no policy routes yet. Add `GET /v1/projects/{project_id}/policy` (READ_ONE) and `PUT` (WRITE, session, idempotent) in `projects/router.py`. In `projects/api.py`, add `PolicyIn{gated, allowed, version}` and `update_policy`, with these rules:
     - versioned update of `project_policies`; 409 `stale_version` with `current` = PolicyOut;
     - 422 when a class is in both lists;
     - emit `PolicyChangedV1(before, after: PolicyDoc)` (`projects/events.py`; nothing emits it yet);
     - write the audit row `policy.changed` (ARCHITECTURE line 409).
     - Add an audit case to `tests/audit_cases.py`. Run `make gen`.
   - UI:
     - `PolicyEditor.tsx` goes inside `rail/SettingsSection.tsx`. P2-09 edits `RailSections.tsx`, so stay out of it. Use `apiWrite({kind:"update", method:"PUT", ...})` and `ConflictError.current`, as `LocalDecisionsSwitch` does.
     - `QuestionItem.tsx` needs a textbox named "Answer" and a button "Answer" (e2e A2.2). It sends payload `{answer}`: the generic `ANSWER` editor in `review/slots.tsx` sends `{text}`, which the backend rejects.
     - `ApprovalItem.tsx`: approve and deny with reason presets; a blank reason is blocked.
     - Wire both into `ReviewQueue.tsx` like `ResultItem`.
   - e2e `A2.2-question.spec.ts` stays `test.fail()` unless it genuinely passes.
4. Final report per the original prompt.

## Deviations (all in the PR body)

1. Keyless call answers 200 `denied` / `run_token_required` (Scott item).
2. `HumanWaitOut.tainted`.
3. The long poll reads the row, not DBOS events.
4. REST twins are `idempotent=False`.
5. Known verdicts open in the request transaction.
6. Deterministic workflow ids (DBOS 3.1.0 has no dedup on partitioned queues).
7. Action classes use P0-17's stored vocabulary, not the plan's P2-05 `proxmox_destructive` / `proxmox_create_start`. This conflicts with the coordinator note (7): migrating would change locked T-P0-17-16. It's a Scott item.
8. `settings.human_wait_poll_seconds` isn't wired; `configure_human_waits` is the knob.
9. No audit cases for `agent.gated_action` / `approval.auto` (task-token actor); covered by T-12 and `test_default_policy.py`.
10. The decisions fake is loaded via importlib in `_human.py`.
11. T-06 is still marked.

## Scott items

- T-P2-05-13 vs the locked P2-01 sweeps: keyless gated calls answer 200 `denied` instead of 403.
- The action-class vocabulary in the plan's P2-05 interface (lines 12706-12713) disagrees with P0-17. The code follows P0-17.

## Verify

```
SEMGREP_SETTINGS_FILE=<your tmp>/semgrep.yml SEMGREP_LOG_FILE=<your tmp>/semgrep.log SEMGREP_VERSION_CACHE_PATH=<your tmp>/semgrep.ver PYTEST_XDIST_AUTO_NUM_WORKERS=3 make check
```

Integration: CI is the authority. A local `make test-int` is allowed at most once per continuation, and a single Docker test can't be run from the sandbox. The rtk output filter condenses long outputs: `rtk recall <id>` gets the full text. Write helper scripts with the Write tool (heredocs and shell variables near git get refused). Helpers in `/tmp/claude-1002/P2-05-c2/`: `unmark.py <file> <test>...`, `section.py <log> <test>`, `threads.sh`, `reply.sh`.

Docs: Context7 `/dbos-inc/dbos-docs` (application versions, cancel, get_workflow_status) and `/modelcontextprotocol/python-sdk` (the session manager is single-use). Both are cited in the PR body.
