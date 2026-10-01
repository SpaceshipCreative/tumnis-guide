// The browser speech engine (P4-03, FR-11.7): the zero-config voice, the Web Speech API's
// speech synthesis on the device itself, so the text never leaves it. `speak` queues one
// utterance and settles on its `end` or `error` event; an abort calls `cancel()`, which
// empties the queue (MDN, SpeechSynthesis:
// https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis).
import type { SpeechEngine, VoiceMessage } from "./types";

/** Whether this browser can speak at all. */
export function speechSupported(): boolean {
  return (
    typeof globalThis.speechSynthesis !== "undefined" &&
    typeof globalThis.SpeechSynthesisUtterance !== "undefined"
  );
}

export function createBrowserEngine(): SpeechEngine {
  return {
    speak: (message: VoiceMessage, signal?: AbortSignal) =>
      new Promise<void>((resolve) => {
        if (!speechSupported() || signal?.aborted) {
          resolve();
          return;
        }
        const synth = globalThis.speechSynthesis;
        const utterance = new SpeechSynthesisUtterance(message.text);
        let settled = false;
        const settle = () => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener("abort", stop);
          resolve();
        };
        const stop = () => {
          synth.cancel();
          settle();
        };
        // An engine error never blocks the queue: the machine moves on either way.
        utterance.onend = settle;
        utterance.onerror = settle;
        signal?.addEventListener("abort", stop);
        synth.speak(utterance);
      }),
  };
}

/** iOS Safari speaks only after a user gesture: call this inside the click that turns
 * voice on, so later messages can speak without one (plan default). */
export function unlockSpeech(): void {
  if (!speechSupported()) return;
  globalThis.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
}
