// Voice mode in the focus bar (P4-03, FR-10.8, FR-11.7): each new focus message the server
// marks `speak` is said once, in order, through the voiceMode machine. The text is exactly
// the in-app message. A message with a server clip plays the clip; without one the browser
// speaks it. With the server engine a clip may land a moment after its message (the worker
// makes it, and a live `speech_clip` message refetches the bar), so a message without one
// waits for it (at most CLIP_WAIT_MS) before it is said.
//
// Messages already there when the bar first loads are never said: a refresh replays
// nothing. The voice settings are read only once a spoken message shows up, so pages with
// voice off make no extra request.
import { useQuery } from "@tanstack/react-query";
import { useActorRef } from "@xstate/react";
import { useEffect, useRef } from "react";

import type { FocusMessageOut } from "../../api/types.gen";
import { voiceMode } from "../../machines/voiceMode";
import { apiUrl } from "../fetch";
import type { VoiceMessage } from "./types";
import { readVoice, voiceQuery } from "./voiceSettings";

/** How long a message without a clip waits for one before it is said (server engine, or
 * the engine not known yet). */
export const CLIP_WAIT_MS = 3_000;

/** The message to say; its clip only when the server engine is (or may be) the one chosen:
 * a clip made earlier outlives a switch to this device's voice. */
function toVoice(message: FocusMessageOut, useClip: boolean): VoiceMessage {
  return !useClip || message.clip_id === null
    ? { id: message.id, text: message.message }
    : {
        id: message.id,
        text: message.message,
        clipUrl: apiUrl(`/speech/clips/${message.clip_id}`),
      };
}

export function useVoiceMode(
  messages: readonly FocusMessageOut[] | undefined,
  { machine = voiceMode, clipWaitMs = CLIP_WAIT_MS } = {},
): void {
  const anySpoken = messages?.some((m) => m.speak) ?? false;
  const voiceRead = useQuery({ ...voiceQuery(), enabled: anySpoken });
  const voice = voiceRead.data ? readVoice(voiceRead.data) : null;
  const on = anySpoken && (voice === null || voice.enabledLevels.length > 0);
  const engine = voice?.engine ?? null;
  const actor = useActorRef(machine);

  useEffect(() => {
    actor.send({ type: on ? "ENABLE" : "DISABLE" });
  }, [actor, on]);

  const seen = useRef<Set<string> | null>(null);
  const latest = useRef(messages);
  // Spoken messages still waiting for a clip: message id -> the timer that gives up.
  const waiting = useRef(new Map<string, number>());

  useEffect(() => {
    latest.current = messages;
    if (messages === undefined) return;
    if (seen.current === null) {
      seen.current = new Set(messages.map((m) => m.id));
      return;
    }
    const ready = (m: FocusMessageOut) =>
      m.clip_id !== null || engine === "browser";
    const say = (m: FocusMessageOut) => {
      const timer = waiting.current.get(m.id);
      if (timer !== undefined) window.clearTimeout(timer);
      waiting.current.delete(m.id);
      actor.send({
        type: "SAY",
        message: toVoice(m, engine !== "browser"),
      });
    };
    for (const message of messages) {
      if (waiting.current.has(message.id)) {
        if (ready(message)) say(message);
        continue;
      }
      if (seen.current.has(message.id)) continue;
      seen.current.add(message.id);
      if (!message.speak) continue;
      if (ready(message)) {
        say(message);
        continue;
      }
      const timer = window.setTimeout(() => {
        say(latest.current?.find((m) => m.id === message.id) ?? message);
      }, clipWaitMs);
      waiting.current.set(message.id, timer);
    }
  }, [actor, messages, engine, clipWaitMs]);

  useEffect(() => {
    const pending = waiting.current;
    return () => {
      for (const timer of pending.values()) window.clearTimeout(timer);
      pending.clear();
    };
  }, []);
}
