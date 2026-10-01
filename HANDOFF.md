# P4-05 handoff (Browser push), continuation c1 -> c2

**PR #140** (https://github.com/SpaceshipCreative/tumnis-guide/pull/140) is open as a
**DRAFT**, opened early so CI could run the Docker layers. CodeRabbit has not been asked
yet: it skips drafts. Branch `wp/P4-05`; push only with
`/usr/bin/git push origin HEAD:wp/P4-05`. Binding instructions:
`~/tumnis-coordinator/prompts/wave1/P4-05.txt` plus the rule files it names. Use the
scratch folder `$TMPDIR/P4-05-c2/`. Copy `check.sh` (fix its cd path to your worktree)
and `unmark.py` from `/tmp/claude-1002/P4-05-c1/`. The **full PR body draft** is
`/tmp/claude-1002/P4-05-c1/pr-body.md`.

The VM disk is nearly full (about 170M free). Keep writes small, and delete your own logs
after reading them.

## Commits (all on top of main 8e4c88c; everything except this handoff is pushed)

| SHA | What |
| --- | --- |
| 618c701, 829e7e4 | spec tests red (backend, frontend) |
| ac99db9, e92b43d | rules + P2-16 seam; WebPush client and fake (T-01..03, T-07 green) |
| 139eb43 | push channel: migration notifications_0001, models, api, events, workflows, router, worker queue, make gen |
| eb75ecf | frontend subscribe flow (T-09 green, both markers removed) |
| c58cf98, a5f634f, c256602 | T-04, T-05, T-06 unmarked (XPASS(strict) in CI run 36871065733); row_factory COLUMN_VALUES for the new check constraints |
| 56e0f43 | service worker via injectManifest (T-08 green, unmarked); workbox-core/precaching/routing 7.4.1 pinned as devDeps |
| c13dccb | removed the c0 HANDOFF |
| a4a421b | DELETE param renamed `{push_subscription_id}` (the A0.3 sweep could not map `{subscription_id}` to a table); SW click calls focus() then navigate(); make gen. **Not pushed yet when written; it is pushed together with this handoff.** `make check` was NOT re-run after a4a421b: run it first. |

## Spec test state
- T-01..09: green and unmarked.
- **T-P4-05-10 (e2e) stays red, marker kept.** It needs the fake runner to play `ask_human`
  (`agents/adapters/fake.py` refuses it on main; that playback is decision 55's work,
  outside this WP). Listed as a Scott item; main was told. Never remove its marker
  locally (decision 51).

## CI state
- Run 36871065733 (at 139eb43): everything green except integration. Integration failed
  only because the two-workspace isolation sweeps errored at setup (row_factory did not know the new
  check constraints; fixed in c58cf98). The push tests XPASSed there.
- Run 36876583751 (at c13dccb): integration, e2e and performance were pending at handoff.
  It is superseded by the push of a4a421b plus this handoff; read the new run.
- **First real check of the new service worker**: e2e (A0.2 offline, CSP spec) and
  performance (Lighthouse) must be green on the new SW. Also the A0.3/authz/CSRF sweeps,
  which never ran on the push routes before (they errored at setup).

## Next steps
1. `bash $TMPDIR/P4-05-c2/check.sh` (make check) on a4a421b; fix anything it finds.
2. Wait for CI on the latest push: `gh pr checks 140`; failures via
   `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs`
   (needs allowed_domains `*.blob.core.windows.net`), grep, then delete the log.
   If `performance` fails only on a page's TBT, re-run once.
3. When everything but `preview` is green (T-10 counts as an expected failure):
   - `gh pr edit 140 --body-file /tmp/claude-1002/P4-05-c1/pr-body.md`. Update the CI
     line and the run ids, and add a4a421b to the body.
   - `gh pr ready 140`, then **once** `gh pr comment 140 --body "@coderabbitai review"`.
4. Review loop (`~/tumnis-coordinator/pr-review-loop.md`), then SendMessage main
   `#140 MERGE-READY at <sha>`.
5. Delete this HANDOFF.md in a chore commit before MERGE-READY.

## Decisions and deviations (all in the PR body draft)
- P2-16 seam built here (coordinator-approved): delivery_decision/flush_due, the
  notifications and delivery_attempts tables (with channel), subscribers on review_item.added
  and focus.event, flushes on task.status_changed (leaving in_progress),
  focus.level_changed and focus.event day_end. T-05 checks push against P2-16's table.
- No pywebpush: http_ece + py_vapid (its own building blocks) through guarded_client;
  main was told.
- The VAPID key pair is made on first use per workspace and sealed in workspace_settings
  (`notifications.vapid`); its subject is `mailto:` the user who enabled push.
- Table shape differs from Part A's (`decision`/`released_at` replace `batched_until`;
  nullable target for batch rows). DELETE param is `{push_subscription_id}`.
- Focus pushes open `/`. An unreadable push shows a generic notification (userVisibleOnly).
- Retries run inside `notifications.deliver_push` (30 s, 2 min); no dead letter for push.
- Contract ids are `TestWebPushFake/Real::test_contract`. Size is closer to M than S.

## Shared-file edits
backend/pyproject.toml + uv.lock (http-ece 1.2.1, py-vapid 1.9.4); frontend/package.json +
lock (workbox-* 7.4.1 devDeps); backend/tumnis/worker.py (notifications queue);
row_factory.py; frontend live-map.ts, AccountSection.tsx, vite.config.ts,
routes/review.tsx (schema moved to lib/reviewSearch.ts), lib/serviceWorker.ts (comment);
generated schemas/openapi.json + frontend/src/api.

## Scott items
- T-10 is blocked on fake-runner `ask_human` playback (decision 55 work).
- Manual: a push on Scott's iPhone (Home Screen app, iOS 16.4+) and on a laptop, against
  an HTTPS deployment.
