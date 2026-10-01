// The voiceMode machine (P4-03, FR-10.8): focus messages are spoken one at a time, in the
// order they arrived, each once; off ignores them and DISABLE drops what is queued. The
// engines are fakes from src/test/speech.ts; machines.test.ts checks R-38 for this file.
import fc from "fast-check";
import { afterEach, expect, test, vi } from "vitest";
import { type AnyStateMachine, createActor } from "xstate";

import { controlledEngine, fakeEngine, type FakeMessage } from "../test/speech";

interface VoiceModule {
  voiceModeWith: (engine: {
    speak: (message: FakeMessage, signal?: AbortSignal) => Promise<void>;
  }) => AnyStateMachine;
}

// Loaded at run time, so this file compiles before voiceMode.ts exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

interface Span {
  id: string;
  start: number;
  end: number;
}

interface VoiceSnapshot {
  value: unknown;
  context: { queue: FakeMessage[]; current?: FakeMessage };
}

const say = (id: string) => ({
  type: "SAY" as const,
  message: { id, text: `Message ${id}` },
});

afterEach(() => {
  vi.useRealTimers();
});

test.fails(
  "[P4-03][FR-10.8] T-P4-03-01 queues so two messages never overlap",
  async () => {
    vi.useFakeTimers();
    const { voiceModeWith } = await load<VoiceModule>("./voiceMode");
    const engine = fakeEngine({ durationMs: 1000 });
    const actor = createActor(voiceModeWith(engine));
    actor.start();
    actor.send({ type: "ENABLE" });

    actor.send(say("1"));
    await vi.advanceTimersByTimeAsync(4);
    actor.send(say("2"));
    await vi.advanceTimersByTimeAsync(4);
    actor.send(say("3"));
    for (let i = 0; i < 3; i += 1) await vi.advanceTimersByTimeAsync(1000);

    expect(engine.log).toEqual(["s1", "e1", "s2", "e2", "s3", "e3"]);
    expect(engine.intervals).toHaveLength(3);
    const [a, b, c] = engine.intervals as [Span, Span, Span];
    expect(a.end).toBeLessThanOrEqual(b.start);
    expect(b.end).toBeLessThanOrEqual(c.start);
    actor.stop();
  },
);

type Step =
  | { type: "SAY"; id: string }
  | { type: "SKIP" }
  | { type: "ENABLE" }
  | { type: "DISABLE" }
  | { type: "DONE" }
  | { type: "ERROR" };

const STEP: fc.Arbitrary<Step> = fc.oneof(
  fc
    .constantFrom("1", "2", "3", "4", "5")
    .map((id): Step => ({ type: "SAY", id })),
  fc.constantFrom<Step>(
    { type: "SKIP" },
    { type: "ENABLE" },
    { type: "DISABLE" },
    { type: "DONE" },
    { type: "ERROR" },
  ),
);

/** Lets settled promises reach the machine (onDone, onError) before the next step. */
async function flush(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

test.fails(
  "[P4-03][FR-10.8] T-P4-03-02 property: never more than one active utterance",
  async () => {
    const { voiceModeWith } = await load<VoiceModule>("./voiceMode");
    await fc.assert(
      fc.asyncProperty(fc.array(STEP, { maxLength: 40 }), async (steps) => {
        const engine = controlledEngine();
        const actor = createActor(voiceModeWith(engine));
        actor.start();
        for (const step of steps) {
          if (step.type === "SAY") actor.send(say(step.id));
          else if (step.type === "DONE") engine.finish();
          else if (step.type === "ERROR") engine.fail();
          else actor.send({ type: step.type });
          await flush();
          expect(engine.state.active).toBeLessThanOrEqual(1);
        }
        actor.stop();
        expect(engine.state.maxActive).toBeLessThanOrEqual(1);
        expect(new Set(engine.state.started).size).toBe(
          engine.state.started.length,
        );
      }),
      { numRuns: 200 },
    );
  },
);

test.fails(
  "[P4-03] T-P4-03-03 off ignores SAY and DISABLE clears the queue",
  async () => {
    const { voiceModeWith } = await load<VoiceModule>("./voiceMode");
    const engine = controlledEngine();
    const actor = createActor(voiceModeWith(engine));
    actor.start();
    const snap = () => actor.getSnapshot() as unknown as VoiceSnapshot;

    expect(snap().value).toBe("off");
    actor.send(say("1"));
    await flush();
    expect(snap().value).toBe("off");
    expect(snap().context.queue).toEqual([]);
    expect(engine.state.started).toEqual([]);

    actor.send({ type: "ENABLE" });
    actor.send(say("2"));
    actor.send(say("3"));
    actor.send(say("4"));
    await flush();
    expect(snap().value).toBe("speaking");
    expect(snap().context.current?.id).toBe("2");
    expect(snap().context.queue.map((m) => m.id)).toEqual(["3", "4"]);

    actor.send({ type: "DISABLE" });
    await flush();
    expect(snap().value).toBe("off");
    expect(snap().context.queue).toEqual([]);
    expect(snap().context.current).toBeUndefined();
    expect(engine.state.active).toBe(0);
    expect(engine.state.started).toEqual(["2"]);
    actor.stop();
  },
);
