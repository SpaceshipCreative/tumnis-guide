// The clip engine (P4-03, FR-11.7): plays a clip the server engine (Piper through the
// Speech slot) made for a message, `GET /v1/speech/clips/{id}`, in an HTMLAudioElement.
// It settles on `ended` or `error`; an abort pauses it. A message without a clip falls back
// to the browser's own speech, so voice still works when the server engine is down.
import { createBrowserEngine } from "./browserEngine";
import type { SpeechEngine, VoiceMessage } from "./types";

export function createClipEngine(
  makeAudio: () => HTMLAudioElement = () => new Audio(),
): SpeechEngine {
  return {
    speak: (message: VoiceMessage, signal?: AbortSignal) =>
      new Promise<void>((resolve) => {
        if (message.clipUrl === undefined || signal?.aborted) {
          resolve();
          return;
        }
        const audio = makeAudio();
        let settled = false;
        const settle = () => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener("abort", stop);
          resolve();
        };
        const stop = () => {
          audio.pause();
          settle();
        };
        audio.addEventListener("ended", settle);
        audio.addEventListener("error", settle);
        signal?.addEventListener("abort", stop);
        audio.src = message.clipUrl;
        // Autoplay refused: nothing to wait for. Some environments return no promise.
        const played = audio.play() as Promise<void> | undefined;
        if (played === undefined) settle();
        else played.catch(settle);
      }),
  };
}

/** Plays the server clip when a message has one, else speaks it in the browser. */
export function createRoutedEngine(
  clip: SpeechEngine = createClipEngine(),
  browser: SpeechEngine = createBrowserEngine(),
): SpeechEngine {
  return {
    speak: (message, signal) =>
      message.clipUrl === undefined
        ? browser.speak(message, signal)
        : clip.speak(message, signal),
  };
}
