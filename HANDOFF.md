# HANDOFF: P2-16 impl-2 (notification delivery), continuation c0

Stopped on HANDOFF NOW from the context watcher. Branch `wp/P2-16-impl-2` (pushed), draft
PR **#150** "[P2-16] impl-2: notification delivery". Instructions:
`~/tumnis-coordinator/prompts/wave1/P2-16-impl-2.txt` (read it and the files it lists).

## Commits (on top of main c7d9062, then main f9b0374 merged in)

| SHA | What |
| --- | --- |
| 8f8476eb | `test(notifications): P2-16 spec tests (red)`: T-P2-16-03 (unit), T-P2-16-04, 05, 10, 11 (integration), all `xfail(strict, spec:P2-16)` |
| 3bbb18c5 | `feat(notifications): channels_now ...` T-P2-16-03 green, marker removed |
| eb255f56 | `feat(notifications): deliver notifications to Discord ...` the delivery path (below) |
| 6d0b141b | merge origin/main (#145 P4-02, #149); `make gen` clean, `alembic heads` single per module |
| fd66aa84 | `feat(profiles): the master's focus skill words review items and batches` |
| (this) | `chore: P2-16-impl-2 handoff` |

## What is built

- `notifications/rules.py`: `Channel`, `channels_now(decision)`: in_app always; discord and
  push only for "now".
- Migration `notifications_0002` (`0002_discord_delivery.py`, down `notifications_0001`):
  `notifications.details` jsonb; `delivery_attempts.run_id`, `error`; channel check widened
  to `('push','discord')` NOT VALID (downgrade re-adds the narrow one NOT VALID).
- `notifications/payloads.py`: event `notification.ready` v1 `{notification_id, kind}`
  (fixture + schema generated). `api._record` emits it in the row's transaction when the
  decision is "now" (only on a fresh insert); `api.flush` emits it for the batch row
  (`details={"count": n}`).
- `notifications/api.py`: `notify_request(ctx, id) -> NotifyFacts | None` (focus event /
  review item / batch body; None when gone or the item was already decided),
  `record_discord_attempt(...)`. `record_focus_event(..., details=)` (events.py passes the
  event's task_id, level, rule, fired_at, return_to_task_id).
- `notifications/events.py`: subscriber `notification.ready` -> `notifications.deliver_notification`
  calls `workflows.start_discord(envelope)`; `_push_now` uses `channels_now`.
- `notifications/workflows.py`: workflow `notifications.deliver_notification(ws, id,
  envelope, max_attempts, base_delay_s)` on the `notifications` queue: master via
  `agents.master_agent` (not provisioned -> "skipped", nothing recorded), then per attempt
  a child `run_skill` (`agents.notify_packet` + `agents.run_notify`, run id uuid5 of the
  round's workflow id + attempt), a `delivery_attempts` row, full-jitter delay step; after
  the last failure `core.deadletter.DeadLetterRepo.record(event_id, subscriber
  "notifications.deliver_notification", envelope)`, so Settings retry/discard works (a
  retry is a new deliver_event round -> new workflow id via `DBOS.workflow_id` read inside
  the subscriber's step, verified in dbos 3.1.0 source: `is_within_workflow` is true in
  steps). Test seam `use_discord(max_attempts=, base_delay_s=)`; `use()` resets it.
  Defaults: 3 attempts, 30 s base, 300 s cap.
- agents (additive): `skill_io.NotifyRequest` (`packet/notify_request/1`), `NotifyEvent`,
  `NotifyItem`, `NotifyBatch`, `NotifyTask`, `NotifyProject`, `NotifyReturnTo`,
  `FocusMessage` (`result/focus_message/1`); `api.notify_packet`, `api.run_notify`
  (= run_plan's child runner), `NOTIFY_SKILL="focus"`, `NOTIFY_TIMEOUT_S=60`. Unit test
  `agents/tests/unit/test_notify_packet.py` (harness focus_* bodies validate as NotifyRequest).
- Profiles: focus SKILL.md handles `event`, `item` (ref `<kind>:<id>` line; approvals and
  results "decide it in Tumnis: <link>") and `batch`; harness `NotifyItemSpec`,
  `NotifyBatchSpec` built with agents' NotifyRequest; packets `focus_question_item.json`,
  `focus_batch.json`; cases `focus/question_item.yaml`, `focus/batch_waiting.yaml`
  (xfail spec:P2-16, homelab). Master profile 1.2.0 -> 1.2.1.
- Tests: `notifications/tests/integration/{_delivery.py,test_delivery.py}`, `delivery`
  fixture in that conftest; `_push.py` WORKFLOWS now also waits on
  `notifications.deliver_notification` and `run_skill` (additive).

## State

- `make check` green at fd66aa84 (use `/tmp/claude-1002/P2-16-c0/check.sh`, which sets the
  SEMGREP_* env vars; `~/.semgrep` is read-only).
- Contract layer green locally (159). Integration tests cannot run in the sandbox (Docker
  socket blocked); CI is the authority. CI run 36946891100 (at 6d0b141b) had unit,
  contract, lint, security, skills, spec-guard, traceability green; integration-a/b, e2e,
  performance were still pending when I stopped. The push of the handoff commit restarts CI.
- T-P2-16-04, 05, 10, 11 still carry `xfail(strict=True, reason="spec:P2-16")`. Not yet
  proven: remove each marker only after CI shows it XPASS(strict) (Scott-approved step).
- PR #150 is a DRAFT (opened early to get CI for the Docker tests; CodeRabbit skips drafts).
  No CodeRabbit review requested yet.

## Remaining steps

1. Wait for CI integration-b on the latest push (notifications tests run there). For each of
   T-P2-16-04/05/10/11: if XPASS(strict), remove that marker (one commit each or one
   commit citing the run). If a test fails for real, read `gh run view <id> --log-failed`
   and fix. Likely risks: settle timing (`PushWorld.settle` 30 s), the fake runner's
   `focus` script, `runs.packet` storing the body, dead letter listing shape
   (`GET /v1/dead-letters` -> `items`).
2. Check the other notifications tests (T-P4-05-*) stay green: with no master they must
   record no discord attempts (T-P4-05-06 asserts every attempt is channel push).
3. Docs: add `notification.ready` to Part A's event catalogue (A8) and the notifications
   table line (`details`, `run_id`, `error`) in `docs/IMPLEMENTATION-PLAN-DETAILED.md`
   (definition of done: new shared names in Part A).
4. Write the full PR body (template in the prompt file step 4: summary, per-layer results,
   shared-file edits, deviations, Scott items, docs cited, ending with the Claude Code
   line), `gh pr edit 150 --body-file ...`, `gh pr ready 150`, then
   `gh pr comment 150 --body "@coderabbitai review"` once. Run the review loop
   (`~/tumnis-coordinator/pr-review-loop.md`).
5. When CI is green and CodeRabbit clean: SendMessage to main "#150 MERGE-READY at <sha>".

## Deviations to list in the PR body

1. T-P2-16-03 is made red by the channel-routing rule (`channels_now`), since
   `delivery_decision`/`flush_due` already existed from P4-05's seam.
2. Dead-lettering reuses core's dead-letter queue through a notifications-owned event
   (`notification.ready`), so Settings retry/discard works (REL-3); `deliver_event`
   resolves the dead letter as soon as a retry round starts the workflow and a failing
   round re-opens it.
3. The notify packet's reply schema is `result/focus_message/1` (the harness keeps its own
   `harness/focus_message/1` for the locked cases) and the body is `packet/notify_request/1`
   (no new schema family, so no core edit).
4. Review items and batches also go to Discord through the focus skill (plan Goal: focus
   events and items that need the person); SKILL.md and two new cases cover them.
5. No master provisioned: Discord is skipped silently (in-app only), no attempts, no dead
   letter. Kill switch on: the notify runs are cancelled, so the delivery fails and
   dead-letters.
6. Harness packets for the locked focus cases are unchanged (input-only change would need a
   ruling); the new item/batch packets use agents' NotifyRequest.
7. A temporary `# type: ignore[attr-defined]` was in the red commit for the not-yet-existing
   names; removed when they landed (no assertion changed).

## Scott items

- Done checklist (homelab): a Nudge reaches Discord from the master; a Discord answer shows
  as answered in the app; kill command on the homelab channel, then resume from the app.
- Plan default chosen: Discord delivery gives up after 3 attempts (30 s full-jitter base,
  300 s cap), then dead-letters.

## Docs relied on

- DBOS Python contexts (`DBOS.workflow_id`, `DBOS.step_id`):
  https://github.com/dbos-inc/dbos-docs/blob/main/docs/python/reference/contexts.md, and the
  installed dbos 3.1.0 source (`_dbos.py` `workflow_id`, `_context.py` `is_within_workflow`).
- PostgreSQL 18 ALTER TABLE (ADD CONSTRAINT ... NOT VALID), as in focus_0002's precedent.

## Shared-file edits so far

None of pyproject/uv.lock/Makefile/.importlinter/AGENTS.md. Generated: `schemas/`,
`backend/tests/contract/generated/` (`make gen`). P4-05 test helper `_push.py` WORKFLOWS
(additive). Harness `profiles/harness/packets.py` (additive).

## Verify commands

```bash
/tmp/claude-1002/P2-16-c0/check.sh                      # make check with SEMGREP_* env
cd backend && uv run pytest -q tumnis/modules/notifications/tests/unit tumnis/modules/agents/tests/unit/test_notify_packet.py
cd backend && uv run pytest -q -m contract -n 3 tests/contract
gh pr checks 150 ; gh run view <id> --log-failed
```
