// Settings > Voice (P4-03, FR-10.8, FR-11.7): which focus levels speak their messages (off
// until a level is chosen), and the engine: this device's own voice (the Web Speech API,
// nothing to set up, the text stays on the device) or the server's (Piper on the agent
// server through the Speech slot). A hosted provider stays off unless allowed here, and is
// never used for a local-only project. Saved with one PUT /v1/settings/voice.
//
// iOS Safari speaks only after a user gesture, so ticking a level speaks an empty
// utterance inside that click (`unlockSpeech`), and later messages can speak on their own.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { focusGetCurrentQueryKey } from "../../../api/@tanstack/react-query.gen";
import type { SettingSectionOut } from "../../../api/types.gen";
import {
  ApiError,
  apiWrite,
  ConflictError,
  useWrite,
} from "../../../lib/fetch";
import { LEVEL_TEXT, type Level } from "../../../lib/levelRules";
import { unlockSpeech } from "../../../lib/speech/browserEngine";
import {
  readVoice,
  type Voice,
  VOICE_LEVELS,
  voiceQuery,
  voiceValues,
} from "../../../lib/speech/voiceSettings";
import { BUTTON, ERROR, HEADING, HINT, SECTION } from "../styles";

const CHOICE = "flex min-h-11 items-center gap-2 [overflow-wrap:anywhere]";

export function VoiceSection() {
  const loaded = useQuery(voiceQuery());
  return (
    <section aria-labelledby="voice-title" className={SECTION}>
      <h2 id="voice-title" className={HEADING}>
        Voice
      </h2>
      {loaded.data ? (
        <VoiceForm loaded={loaded.data} />
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          The voice settings could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

function VoiceForm({ loaded }: { loaded: SettingSectionOut }) {
  const queryClient = useQueryClient();
  const name = useId();
  const [section, setSection] = useState(loaded);
  const [draft, setDraft] = useState<Voice>(() => readVoice(loaded));
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const show = (current: SettingSectionOut) => {
    queryClient.setQueryData(voiceQuery().queryKey, current);
    setSection(current);
    setDraft(readVoice(current));
  };

  const save = useWrite<
    { values: Record<string, unknown>; idempotencyKey?: string },
    SettingSectionOut
  >({
    mutationFn: ({ values, idempotencyKey }) =>
      section.version == null
        ? apiWrite<SettingSectionOut>({
            kind: "create",
            method: "PUT",
            path: "/settings/voice",
            body: { values, version: null },
            idempotencyKey,
          })
        : apiWrite<SettingSectionOut>({
            kind: "update",
            method: "PUT",
            path: "/settings/voice",
            body: { values },
            version: section.version,
            idempotencyKey,
          }),
    onSuccess: (saved) => {
      show(saved);
      // Whether each focus message is spoken is read with the bar's state.
      void queryClient.invalidateQueries({
        queryKey: focusGetCurrentQueryKey(),
      });
      setFailed(false);
      setMessage("Saved.");
    },
    onError: async (error) => {
      setFailed(true);
      if (error instanceof ConflictError) {
        const current = await queryClient.query({
          ...voiceQuery(),
          staleTime: 0,
        });
        show(current);
        setMessage(
          "Voice changed elsewhere; you are now seeing the current settings.",
        );
        return;
      }
      setMessage(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The voice settings could not be saved.",
      );
    },
  });

  const toggle = (level: Level, on: boolean) => {
    if (on) unlockSpeech(); // inside the click: iOS needs the gesture
    setDraft((d) => ({
      ...d,
      enabledLevels: on
        ? [...d.enabledLevels.filter((l) => l !== level), level]
        : d.enabledLevels.filter((l) => l !== level),
    }));
  };

  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setMessage(null);
        save.mutate({ values: voiceValues(draft) });
      }}
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">Speak focus messages at</legend>
        <p className={HINT}>
          Off until you choose a level. The spoken words are exactly the message
          shown in the focus bar.
        </p>
        {VOICE_LEVELS.map((level) => (
          <label key={level} className={CHOICE}>
            <input
              type="checkbox"
              className="size-5"
              checked={draft.enabledLevels.includes(level)}
              onChange={(e) => {
                toggle(level, e.target.checked);
              }}
            />
            {LEVEL_TEXT[level]}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">Voice</legend>
        <label className={CHOICE}>
          <input
            type="radio"
            className="size-5"
            name={`${name}-engine`}
            checked={draft.engine === "browser"}
            onChange={() => {
              setDraft((d) => ({ ...d, engine: "browser" }));
            }}
          />
          This device&apos;s voice
        </label>
        <label className={CHOICE}>
          <input
            type="radio"
            className="size-5"
            name={`${name}-engine`}
            checked={draft.engine === "server"}
            onChange={() => {
              setDraft((d) => ({ ...d, engine: "server" }));
            }}
          />
          The server&apos;s voice
        </label>
        <p className={HINT}>
          The server&apos;s voice needs a speech engine on the agent server.
          When it is not reachable, this device speaks instead.
        </p>
      </fieldset>
      {draft.engine === "server" && (
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium">Server engine</legend>
          <label className={CHOICE}>
            <input
              type="radio"
              className="size-5"
              name={`${name}-provider`}
              checked={draft.serverProvider === "piper"}
              onChange={() => {
                setDraft((d) => ({ ...d, serverProvider: "piper" }));
              }}
            />
            Piper (local)
          </label>
          <label className={CHOICE}>
            <input
              type="radio"
              className="size-5"
              name={`${name}-provider`}
              checked={draft.serverProvider === "hosted"}
              onChange={() => {
                setDraft((d) => ({ ...d, serverProvider: "hosted" }));
              }}
            />
            Hosted
          </label>
          <label className={CHOICE}>
            <input
              type="checkbox"
              className="size-5"
              checked={draft.hostedAllowed}
              onChange={(e) => {
                setDraft((d) => ({ ...d, hostedAllowed: e.target.checked }));
              }}
            />
            Allow hosted speech
          </label>
          <p className={HINT}>
            Hosted speech sends the message text to a third party. It is never
            used for a local-only project; those speak on this device.
          </p>
        </fieldset>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={BUTTON} disabled={save.isPending}>
          Save
        </button>
        {message && (
          <p
            role={failed ? "alert" : "status"}
            className={failed ? ERROR : HINT}
          >
            {message}
          </p>
        )}
      </div>
    </form>
  );
}
