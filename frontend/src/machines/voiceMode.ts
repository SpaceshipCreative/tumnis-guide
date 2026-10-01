// The voiceMode machine (P4-03, FR-10.8; XState v5 `setup(...).createMachine`,
// https://stately.ai/docs/setup): focus messages are spoken one at a time, in the order they
// arrived, each once. `off` ignores SAY; `idle` starts the next queued message; `speaking`
// invokes the engine for `current` and queues anything said meanwhile. Leaving `speaking`
// (SKIP, DISABLE) stops the invoked promise actor, whose AbortSignal stops the engine; an
// engine error moves on like an end, so one bad message never blocks the queue.
//
// Deviation from the plan's sketch: DISABLE while speaking marks the message it cut off as
// spoken (`markSpoken` before `clearQueue`), so turning voice off and on again never
// replays it (the property test T-P4-03-02: each id spoken at most once).
import { assign, fromPromise, setup } from "xstate";

import { createRoutedEngine } from "../lib/speech/clipEngine";
import type { SpeechEngine, VoiceMessage } from "../lib/speech/types";

/** How many spoken ids the machine remembers to drop repeats. */
const SPOKEN_MEMORY = 50;

interface Ctx {
  queue: VoiceMessage[];
  current?: VoiceMessage;
  spokenIds: string[];
}

export type VoiceModeEvent =
  | { type: "ENABLE" }
  | { type: "DISABLE" }
  | { type: "SAY"; message: VoiceMessage }
  | { type: "SKIP" };

/** The `speak` actor over one engine. */
export function speakWith(engine: SpeechEngine) {
  return fromPromise<undefined, VoiceMessage | undefined>(
    async ({ input, signal }) => {
      if (input !== undefined) await engine.speak(input, signal);
      return undefined;
    },
  );
}

export const voiceMode = setup({
  types: {
    context: {} as Ctx,
    events: {} as VoiceModeEvent,
  },
  actors: { speak: speakWith(createRoutedEngine()) },
  guards: { hasQueued: ({ context }) => context.queue.length > 0 },
  actions: {
    enqueueIfNew: assign({
      queue: ({ context, event }) => {
        if (event.type !== "SAY") return context.queue;
        const id = event.message.id;
        const seen =
          context.spokenIds.includes(id) ||
          context.current?.id === id ||
          context.queue.some((m) => m.id === id);
        return seen ? context.queue : [...context.queue, event.message];
      },
    }),
    takeHead: assign({
      current: ({ context }) => context.queue[0],
      queue: ({ context }) => context.queue.slice(1),
    }),
    markSpoken: assign({
      spokenIds: ({ context }) =>
        context.current
          ? [...context.spokenIds, context.current.id].slice(-SPOKEN_MEMORY)
          : context.spokenIds,
      current: () => undefined,
    }),
    clearQueue: assign({ queue: () => [], current: () => undefined }),
  },
}).createMachine({
  id: "voiceMode",
  initial: "off",
  context: { queue: [], spokenIds: [] },
  states: {
    off: { on: { ENABLE: "idle" } }, // SAY ignored while off
    idle: {
      always: { guard: "hasQueued", target: "speaking" },
      on: { SAY: { actions: "enqueueIfNew" }, DISABLE: "off" },
    },
    speaking: {
      entry: "takeHead",
      invoke: {
        src: "speak",
        input: ({ context }) => context.current,
        onDone: { target: "idle", actions: "markSpoken" },
        onError: { target: "idle", actions: "markSpoken" },
      },
      on: {
        SAY: { actions: "enqueueIfNew" },
        SKIP: { target: "idle", actions: "markSpoken" },
        DISABLE: { target: "off", actions: ["markSpoken", "clearQueue"] },
      },
    },
  },
});

/** The machine speaking through `engine` (tests pass a fake). */
export function voiceModeWith(engine: SpeechEngine) {
  return voiceMode.provide({ actors: { speak: speakWith(engine) } });
}
