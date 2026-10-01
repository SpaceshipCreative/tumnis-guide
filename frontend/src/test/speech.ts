// Fake speech engines for the voiceMode tests (P4-03). Each implements the engine shape
// `speak(message, signal)` (src/lib/speech/types.ts) and records what it did, so a test can
// see when each message started and ended and whether two ever played at once.

export interface FakeMessage {
  id: string;
  text: string;
  clipUrl?: string;
}

/** Speaks each message for `durationMs` (on whatever timers the test runs), logging
 * `s<id>` at the start, `e<id>` at the end and `a<id>` when aborted, with the times. */
export function fakeEngine({ durationMs }: { durationMs: number }) {
  const log: string[] = [];
  const intervals: { id: string; start: number; end: number }[] = [];
  const speak = (message: FakeMessage, signal?: AbortSignal) =>
    new Promise<void>((resolve) => {
      const start = Date.now();
      log.push(`s${message.id}`);
      const timer = setTimeout(() => {
        log.push(`e${message.id}`);
        intervals.push({ id: message.id, start, end: Date.now() });
        resolve();
      }, durationMs);
      signal?.addEventListener("abort", () => {
        clearTimeout(timer);
        log.push(`a${message.id}`);
        intervals.push({ id: message.id, start, end: Date.now() });
        resolve();
      });
    });
  return { speak, log, intervals };
}

/** An engine the test finishes by hand: `finish()` ends the message playing, `fail()`
 * makes it error. `active` counts messages playing now and `maxActive` the most ever at
 * once; `started` lists every message id the engine was asked to play, in order. */
export function controlledEngine() {
  let pending: { resolve: () => void; reject: (e: Error) => void } | null =
    null;
  const state = { active: 0, maxActive: 0, started: [] as string[] };
  const end = () => {
    state.active -= 1;
    pending = null;
  };
  const speak = (message: FakeMessage, signal?: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      state.active += 1;
      state.maxActive = Math.max(state.maxActive, state.active);
      state.started.push(message.id);
      let settled = false;
      const once = (fn: () => void) => () => {
        if (settled) return;
        settled = true;
        end();
        fn();
      };
      const done = once(resolve);
      const failed = once(() => {
        reject(new Error("engine failed"));
      });
      pending = { resolve: done, reject: failed };
      signal?.addEventListener("abort", once(resolve));
    });
  return {
    speak,
    state,
    finish: () => pending?.resolve(),
    fail: () => pending?.reject(new Error("engine failed")),
  };
}
