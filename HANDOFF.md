# HANDOFF: P4-03 Speech slot and voice mode

Branch `wp/P4-03` (pushed with `/usr/bin/git push origin HEAD:wp/P4-03`). **No PR opened yet.**
CodeRabbit: not requested yet. CI: not run beyond the red push. Handoff on the context
watcher's "HANDOFF NOW" (Scott's instruction).

## Commits

| SHA | What |
| --- | --- |
| 3f31e3aa | `test(decisions): P4-03 spec tests (red)`: T-P4-03-01..09 strict red, `frontend/src/test/speech.ts` fakes |
| 5da94fd9 | merge origin/main (includes #142 P4-01: FocusBar, focus api, focus_0002) |
| f68a44d6 | `feat(frontend)`: voiceMode machine, browser/clip/routed engines (T-01..04 green, markers off) |
| 5f331609 | `feat(decisions)`: SpeechTTS port, FakeTTS/FakeHostedTTS, PiperTTS, HostedTTS, registry, contract classes, recordings (T-05 green, marker off) |
| d263754d | `feat(decisions)`: VoiceSettings + speech_route (rules), speech_slot.py, speech_clips (decisions_0004), `GET /v1/speech/clips/{id}`, Settings.speech, worker configure (T-07 green, marker off) |
| (this) | `chore: P4-03 handoff` |

## Spec tests

| ID | State |
| --- | --- |
| T-P4-03-01/02/03 `frontend/src/machines/voiceMode.test.ts` | green, unmarked |
| T-P4-03-04 `frontend/src/lib/speech/browserEngine.test.ts` | green, unmarked |
| T-P4-03-05 `decisions/tests/contract/test_tts_contract.py::test_tts_contract[fake]` | green, unmarked |
| T-P4-03-06 `...[piper]` | skipped unless `PIPER_URL` (nightly); not run locally (no Piper here) |
| T-P4-03-07 `decisions/tests/unit/test_voice_settings.py` | green, unmarked |
| T-P4-03-08 `notifications/tests/integration/test_voice_delivery.py` | still `xfail(strict)` |
| T-P4-03-09 `decisions/tests/integration/test_speech_clips.py` | still `xfail(strict)`; code exists, unverified (Docker) |

## Remaining steps (in order)

1. **Focus (small, additive; #142 already merged into the branch)**: in
   `backend/tumnis/modules/focus/api.py` add to `FocusMessageOut` `speak: bool = False` and
   `clip_id: UUID | None = None`. In `_current` (builds `messages=[...]`, ~line 480):
   `voice = await decisions.voice_settings(ctx)`; `clips = await decisions.clip_ids(ctx,
   [e.id for e in events], now, session=s)` (only when `voice.enabled_levels`);
   `speak=e.level in voice.enabled_levels`, `clip_id=clips.get(e.id)`.
2. **Notifications (worker side)**: `notifications/events.py` add
   `@subscribe("focus.event", name="notifications.speak_focus_event")` calling
   `workflows.speak_focus_event(ctx, envelope.payload, at=envelope.occurred_at)`.
   `notifications/workflows.py`: `speak_focus_event` reads `decisions.voice_settings(ctx)`;
   returns None unless `payload["level"] in enabled_levels` and engine is `server`; resolves
   the task's project (`tasks.get_task(s, task_id).project_id` inside `tenant_session`) and
   calls `decisions.speak(ctx, payload["message"], message_id=UUID(payload["event_id"]),
   now=at, project_id=...)`; catches `AdapterError` (log field names only, no clip, the PWA
   falls back to browser speech). Keep the additions in a fenced block: #140 (P4-05) also
   rewrites these stub files and adds `notifications.push_focus_event` on the same event.
   No notifications migration (#140 owns notifications_0001).
3. `make gen` (new route `speech_get_clip`, FocusMessageOut fields, voice section is generic).
   Add `speechGetClip` to `NOT_LIVE` in `frontend/src/lib/live-map.ts` if the generator makes a
   query op for it; add LiveEntity `"speech_clip"` -> lists `["focusGetCurrent"]` (decisions
   marks `speech_clip` with the message id when a clip is stored).
4. Check sweeps: `tests/meta/test_table_registry.py` (speech_clips via create_tenant_table),
   route registry/authz matrix (GET, session), `alembic upgrade/downgrade` (decisions_0004 after
   decisions_0003; main's decisions head is decisions_0003).
5. Remove T-08 and T-09 markers only after they pass in CI (`make test-int` at most once
   locally; Docker load rule).
6. Frontend: Settings > Voice (`frontend/src/components/settings/voice/VoiceSection.tsx` +
   test): add `"voice"` to `components/settings/sections.ts` and the `SCREENS`/loader switch in
   `routes/settings.$section.tsx`; read/write `settingsGetSectionOptions({path:{section:"voice"}})`
   and `PUT /settings/voice` `{values, version}`; checkboxes per level, engine radio
   (browser/server), provider (piper/hosted), hosted_allowed toggle. Enabling calls
   `unlockSpeech()` (lib/speech/browserEngine.ts) inside the click (iOS). Check at 375 px.
7. Wiring: a `useVoiceMode(messages)` hook (e.g. `frontend/src/lib/speech/useVoiceMode.ts`) with
   `useActorRef(voiceMode)`: ENABLE when the voice section has any enabled level; seed seen ids
   with the messages present on first load (no replay after refresh); SAY each new message
   with `speak: true` (`clipUrl: /v1/speech/clips/<clip_id>` when set; with engine `server` and
   no clip yet, wait a few seconds for the live refetch, then SAY without a clip so the browser
   speaks it). One line + import in `components/focus/FocusBar.tsx` (keep it minimal; P2-16 and
   #142 touched it).
8. README phone section: iOS needs the enable tap (plan note).
9. Docs: add new shared names to Part A of the plan if required (SpeechTTS, VoiceSettings,
   speech_clips, `voice` section, `speech_clip` live entity).
10. Verify: `bash $TMPDIR/P4-03-c0/check.sh` (make check with semgrep env), `cd backend && uv run
    pytest -m "not integration and not contract" -n 3`, `-m contract`, `make test-int` once,
    Vitest. Then open the PR per the prompt (body file in `$TMPDIR/P4-03-c0/`), comment
    `@coderabbitai review` once, run the review loop, send "#<PR> MERGE-READY at <sha>" to main.

## Decisions and deviations (for the PR body)

- **Delivery seam**: notifications has no delivery on main (P2-16 unmerged; #140 adds push).
  The PWA reads focus messages only from `GET /v1/focus/current`, so `speak` and `clip_id` go on
  `FocusMessageOut`, computed from decisions' voice settings and clips (focus already imports
  decisions; no cycle). Notifications only runs the worker-side synthesis subscriber.
- `speak` flag is computed at read time from current settings (no per-message stored flag:
  avoids a notifications migration that would collide with #140).
- `VoiceSettings`/`speech_route` live in `decisions/rules.py`; `FocusLevel` literal duplicated
  there (decisions cannot import focus: focus imports decisions).
- Hosted refused -> route `browser` (text stays on device); Piper allowed for local-only.
- voiceMode: DISABLE while speaking runs `markSpoken` before `clearQueue` (the plan's sketch
  only cleared), so a cut-off message is never replayed; required by T-P4-03-02's
  "each id spoken at most once". T-02's generator was tightened (ids 1-3, 10-40 steps): a
  mutation check showed the original generator never found the replay.
- Synthesis errors never dead-letter: caught, no clip, browser fallback.
- `HostedTTS` (OpenAI-compatible `/v1/audio/speech`, `response_format: wav`) is built and
  contract-tested but not configured from the environment (key storage = Scott item, same as
  P3-10's hosted embedder).
- Recordings in `decisions/tests/recordings/speech/` are synthetic WAVs (no real host/audio).
- T-P4-03-06 (real Piper) not run here; nightly job needs `PIPER_URL`.

## Shared-file edits so far

- `backend/.importlinter`: `api-never-calls-out` forbids `decisions.adapters.speech.piper` and
  `.hosted`.
- `backend/tumnis/settings.py`: `SpeechSettings` + `Settings.speech` (not a listed shared file).
- No pyproject/uv.lock/package.json changes.

## Docs consulted (cite in PR body)

- Piper HTTP API: https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md
- MDN SpeechSynthesis.speak / cancel: https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis
- OpenAI createSpeech schema (openai/openai-openapi `manual_spec` openapi.yaml, CreateSpeechRequest)
- Context7: XState v5 (`/statelyai/xstate`: fromPromise abort on stop, setup/provide), fast-check 4
  (`/dubzzz/fast-check`: asyncProperty, constantFrom typing in v4)

## Gotchas

- Run make check via `bash /tmp/claude-1002/P4-03-c0/check.sh` (semgrep needs writable state
  files). Compound shell commands with `for`/`export`/heredocs are refused by the worktree
  guard: use script files in `$TMPDIR/P4-03-c0/` or the Edit tool.
- Red tests load code with `importlib.import_module` (mypy and import-linter): the notifications
  test loads the focus test world by name.
- `eslint --fix` on voiceMode.ts strips `{} as Ctx` in `setup({types})`; keep the
  `context: {} as Ctx, events: {} as VoiceModeEvent` form.
