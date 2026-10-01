// The browser speech engine (P4-03, FR-11.7): the PWA's zero-config voice speaks through
// the Web Speech API's `speechSynthesis` (MDN: SpeechSynthesis.speak queues an utterance;
// `cancel` empties the queue). jsdom has no speechSynthesis, so the test stubs it.
import { afterEach, expect, test, vi } from "vitest";

interface BrowserEngineModule {
  createBrowserEngine: () => {
    speak: (
      message: { id: string; text: string },
      signal?: AbortSignal,
    ) => Promise<void>;
  };
}

// Loaded at run time, so this file compiles before browserEngine.ts exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

class FakeUtterance {
  text: string;
  onend: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("[P4-03][FR-11.7] T-P4-03-04 uses speechSynthesis", async () => {
  const spoken: FakeUtterance[] = [];
  const synth = {
    speak: vi.fn((utterance: FakeUtterance) => {
      spoken.push(utterance);
    }),
    cancel: vi.fn(),
  };
  vi.stubGlobal("speechSynthesis", synth);
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  const { createBrowserEngine } =
    await load<BrowserEngineModule>("./browserEngine");
  const engine = createBrowserEngine();

  let ended = false;
  const speaking = engine
    .speak({ id: "m1", text: "Time for Write proposal." })
    .then(() => {
      ended = true;
    });
  expect(synth.speak).toHaveBeenCalledTimes(1);
  expect(spoken[0]?.text).toBe("Time for Write proposal.");
  await Promise.resolve();
  expect(ended).toBe(false);
  spoken[0]?.onend?.(new Event("end"));
  await speaking;
  expect(ended).toBe(true);

  // Aborting (the machine left `speaking`) cancels the utterance and settles at once.
  const abort = new AbortController();
  const stopped = engine.speak(
    { id: "m2", text: "Still on it?" },
    abort.signal,
  );
  abort.abort();
  await stopped;
  expect(synth.cancel).toHaveBeenCalled();
});
