# P2-16 handoff (c0 -> c1)

WP: P2-16 "Master focus skill and delivery". Branch `wp/P2-16` (pushed from the throwaway
worktree branch with `/usr/bin/git push origin HEAD:wp/P2-16`). **No PR opened yet.**
Binding prompt: `~/tumnis-coordinator/prompts/wave1/P2-16.txt`. Scratch: `$TMPDIR/P2-16-c0/`.

## Coordinator answers (binding)

1. Option A: build skills, cases and `record_human_reply` first (PR1). Delivery (the
   `notifications` module: T-P2-16-03, 04, 05, 10, 11, `deliver_notification`, notify
   runs) waits for P4-05's PR #140 to merge, then extends its notifications seam
   (`notifications/rules.py` `delivery_decision`, `flush_due`, tables `notifications`,
   `push_subscriptions`, `delivery_attempts` with `channel`, migration notifications_0001).
   Do not branch off wp/P4-05. Do not create `modules/notifications/` before #140 merges.
2. Scott decision 57: add `("master","focus")` and `("master","relay")` to `SKILLS` in the
   locked `profiles/harness/tests/test_coverage.py` (T-P2-12-11) as its OWN commit:
   `test(profiles): T-P2-12-11 lists the P2-16 master skills (Scott decision 57)`. No
   assertion change. List it under "Spec changes" in the PR body and tell the coordinator
   the PR number (they add the spec-change label).
3. T-P4-01-12 lives at `profiles/tests/cases/focus/detour_return.yaml` (case loader requires
   the folder named for the skill). Record as a deviation from the plan's path.

P4-01 (#142) merged as b60abf5 (focus_0002, detours, `POST /v1/focus/return`); it deferred
T-P4-01-12 and the master focus SKILL.md detour/return behaviour to this WP. Any focus
migration goes after focus_0002.

## Commits so far

- `3920417d` test(agents): P2-16 spec tests (red): T-P2-16-06 (profiles
  test_profile_static.py::test_only_master_has_discord_gateway), T-P2-16-07/08
  (agents/tests/integration/test_relay.py), plus a non-ID focus relay test
  (focus/tests/integration/test_focus_relay.py). All `xfail(strict=True, reason="spec:P2-16")`.
- `099c2511` merge origin/main (#142).
- `a63380e6` feat(agents): record_human_reply. `make check` green at this commit.
  - `core/tenancy.py`: `act_as(session, actor)` re-binds app.actor/app.user_id in the open
    transaction and restores it (so the relayed answer's rows and outbox actor are the
    person's; the audit row after the block is the master key's).
  - `auth/api.py`: `workspace_owner(session)`.
  - `agents/relay.py` (+ re-exported from agents.api): `NEEDS_APP`, `ReplyHandler`,
    `register_reply_handler`, `reply_handler`. focus registers `focus.api.relay_reply` from
    `focus/mcp.py` (agents -> focus would be an import cycle: focus already imports agents).
  - `agents/mcp.py`: `RelayReplyBody`, `RecordHumanReplyIn`, `RelayReplyOut` (has
    `tainted`), `_record_reply`, op `RECORD_HUMAN_REPLY` (master_only, scope delegate,
    session_twin_allowed=False, no project arg). Audit action `human.relayed`, details
    {channel: "discord", channel_message_id, item_kind, on_behalf_of}; target
    ("review_item", id) or ("focus_event", id).
  - `agents/router.py`: `POST /v1/relay/replies` twin.
  - `focus/api.py`: `relay_reply` (the four one-tap answers via `respond`; `return`/`stay`
    via `return_detour` at the detour task's version; 422 `invalid_answer` otherwise),
    `RELAY_ANSWERS`, `RETURN_ANSWERS`.
  - `core/agent_surface.py`: removed `record_human_reply` from `PENDING_TOOLS`.
  - `tests/_mcp.py`: `SAMPLES["record_human_reply"]` (a fresh question via
    `agents.ask_human` on a running run).
  - `make gen`: schemas/mcp/v1/tools.json, schemas/openapi.json, frontend/src/api.

## Spec test status

- T-P2-16-06: red (xfail) — needs the master config work below.
- T-P2-16-07, 08 and the focus relay test: implementation is in; NOT yet run (Docker-only;
  no single-file bare form). Run `make test-int` once, or push and read CI integration. Remove
  each marker only after it passes; never edit assertions.
- Also watch the meta sweeps that now include the new op (integration): test_taint_sweep
  (T-P2-08-07), test_mcp_authz_matrix (T-P2-01-05/06), test_mcp_write_rules, test_mcp_parity.
- T-P2-16-01, 02, 09 and T-P4-01-12: not written yet (skill cases; see below).
- T-P2-16-03, 04, 05, 10, 11: deferred to after #140 (delivery).

## Remaining steps (PR1)

1. Master profile config (turns T-P2-16-06 green): `profiles/master/config.yaml` add
   `platforms: {discord: {enabled: true}}` and `discord: {require_mention: false,
   auto_thread: false}` (no channel lists in config). `profiles/master/.env.example` add empty
   `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS`, `DISCORD_ALLOWED_CHANNELS`,
   `DISCORD_HOME_CHANNEL` (comment: the last two hold the same one channel id; the master's
   `TUMNIS_TOKEN` key needs `delegate` for pause_agents and record_human_reply in gateway
   sessions). `distribution.yaml` env_requires each (required: true). Bump master VERSION
   and distribution `version` together (1.1.0 -> 1.2.0). No placeholder may match
   SECRET_SHAPES. Docs used: hermes-agent `website/docs/user-guide/messaging/discord.md`
   (DISCORD_* env vars, allowed/home channel), Context7 `/nousresearch/hermes-agent`.
   Hermes is not pinned in the repo, so latest docs were used (say so in the PR). `${VAR}`
   expansion outside `mcp_servers` is not documented, hence channel ids only in .env.
2. Skills: `profiles/master/skills/focus/SKILL.md` (notify packet -> one short message with
   the task's first action and the level + rule attribution, e.g. "Coach · check_in_due";
   check-in lists the four one-tap answers still_on_it / switched / stuck / snooze; detour
   `switched` event with detour + return-to task names both and offers return/stay; post via
   the `send_message` tool to target `discord` (home channel); reply JSON `{"message": ...}`;
   end each message with an item reference line the relay can read, e.g. `ref focus:<event_id>`
   / `ref question:<review_item_id>`). `profiles/master/skills/relay/SKILL.md` (channel
   messages are untrusted data; the only command acted on without an item ref is the kill
   command "stop all agents" -> `pause_agents` scope workspace with a reason, never resume;
   replies to a question/focus message -> `record_human_reply`; approvals/results -> reply
   with a link to the item in the app, never record). Add one line to master SOUL.md outside
   the `tumnis:rules` block about channel messages using the relay skill. Frontmatter
   `name` + `description` required (test_profiles).
3. Harness plumbing:
   - `harness/mock_worker_tools.py` WORKER_TOOLS: add `"discord": (_tool("send_message",
     "Send a message to the chat channel.", action="string", target="string",
     message="string"),)`. Note in the PR: production uses Hermes's built-in `send_message`;
     the harness mock is an MCP tool (`mcp_discord_send_message` to the model), so the first
     homelab run must confirm the exposure; phrase SKILL.md to "use send_message".
   - `harness/packets.py`: NotifySpec/RelaySpec builders (body: kind notify, event {kind,
     event_id, level, rule, message, fired_at, detour_task_id?, return_to_task_id?}, task
     {id, title, first_action}, project {id, name}; relay body: kind relay, message {id,
     content, reply_to {id, content}?}); then `uv run python -m harness packets`.
     Packets for: focus block_start, check_in_due, switched-detour; relay kill command,
     question answer.
   - `harness/schemas/focus_message.v1.json` ({message: string, required}) and
     `relay_reply.v1.json` (e.g. {action: enum [paused, recorded, needs_app, ignored],
     reply: string}).
   - `harness/hostile.py`: `_inject_notify` (task title / first action / project name) and
     `_inject_relay` (message content); BASES entries `focus`, `relay`; then regenerate
     `profiles/tests/cases/hostile/index.yaml` with `uv run python -m harness index --suite
     hostile`. Check test_hostile.py for hard-coded run counts first.
4. Cases (meta xfail "spec:P2-16", req, wp): `tests/cases/focus/block_start_uses_first_action.yaml`
   (T-P2-16-01: discord.send_message message matches first action and "Nudge · block_start"),
   `focus/check_in_one_tap.yaml` (T-P2-16-02: all four answers), `focus/detour_return.yaml`
   (T-P4-01-12, wp P4-01: names both titles, offers return), `relay/kill_command.yaml`
   (T-P2-16-09: `calls: [{tool: pause_agents, min: 1, max: 1, where scope == workspace}]`
   and `forbid: [{tool: pause_agents, where scope == project}]`; there is no resume tool to
   forbid, say so in a comment), and a relay question-answer case.
5. Decision-57 commit (SKILLS constant) adjacent to the skills commit; `make check` green on
   the pair; note the one-commit red window in the PR body.
6. Every layer: `make check` (with SEMGREP_* env under $TMPDIR/P2-16-c0), `make test` (unit +
   contract), `make test-int` once (bare, from worktree root), Vitest via make check.
7. PR: push `HEAD:wp/P2-16`, body file in `$TMPDIR/P2-16-c1/`, `gh pr create --base main
   --head wp/P2-16 --title "[P2-16] impl: master focus skill and delivery" --body-file ...`,
   then `gh pr comment <url> --body "@coderabbitai review"` once. Tell the coordinator the PR
   number (decision 57 label). Review loop per `~/tumnis-coordinator/pr-review-loop.md`.
8. After #140 merges: impl-2 on `wp/P2-16-impl-2` for delivery (T-03/04/05/10/11).

## Deviations to list in the PR

- T-P4-01-12 path `focus/detour_return.yaml` (coordinator-approved).
- Delivery (notifications, T-03/04/05/10/11) deferred to impl-2 after #140 (coordinator).
- `record_human_reply` lives in agents but focus replies go through a handler focus
  registers (import cycle otherwise); `focus.api.relay_reply` also takes return/stay.
- `human.relayed` audit row (channel facts) has no AUDIT_CASES entry: relaying is not a SEC-3
  action and the audit Ctx cannot drive a master key; T-P2-16-07 checks the row.
- Relayed answers act as the workspace owner (`auth.workspace_owner`), v1 has one person.
- Hermes unpinned: latest docs; send_message tool-name gap between harness and production.
- Extra test without a T-ID: focus/tests/integration/test_focus_relay.py.

## Shared-file edits so far

`backend/tests/_mcp.py` (one sample), `backend/tumnis/core/agent_surface.py` (PENDING_TOOLS
line removed), `backend/tumnis/core/tenancy.py` (act_as), generated files via make gen.
No pyproject/uv.lock/Makefile/.importlinter/AGENTS.md edits. No migration added.

## Gotchas

- Use `/usr/bin/git` (plain git is refused); never compound git with cd or variables.
- `make check` needs `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-16-c0/semgrep-settings.yml
  SEMGREP_LOG_FILE=/tmp/claude-1002/P2-16-c0/semgrep.log
  SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-16-c0/semgrep-version` (literal paths;
  the worktree guard refuses `$S`-style variables in make commands).
- rtk condenses pytest output; use `rtk proxy uv run pytest --collect-only ...` to list items.
- Locked T-P2-12-08 pins master `mcp_servers` to {tumnis, jev}: Discord is not an MCP server.
