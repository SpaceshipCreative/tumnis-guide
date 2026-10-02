// The clip engine (P4-03, FR-11.7): plays a clip the server engine (Piper through the
// Speech slot) made for a message, `GET /v1/speech/clips/{id}`, in an HTMLAudioElement.
// It resolves on `ended` (or at once when aborted, after pausing) and rejects when the clip
// cannot play: an `error` event (a 404 after the clip expired, the network) or a refused
// `play()` (MDN, HTMLMediaElement.play():
// https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/play). The routed
// engine then speaks the same text in the browser, so voice still works when the server
// engine is down or its clip is gone.
import { createBrowserEngine } from "./browserEngine";
import type { SpeechEngine, VoiceMessage } from "./types";

export class ClipFailed extends Error {
  constructor() {
    super("the clip could not be played");
    this.name = "ClipFailed";
  }
}

export function createClipEngine(
  makeAudio: () => HTMLAudioElement = () => new Audio(),
): SpeechEngine {
  return {
    speak: (message: VoiceMessage, signal?: AbortSignal) =>
      new Promise<void>((resolve, reject) => {
        if (message.clipUrl === undefined || signal?.aborted) {
          resolve();
          return;
        }
        const audio = makeAudio();
        let settled = false;
        const settle = (failed: boolean) => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener("abort", stop);
          if (failed) reject(new ClipFailed());
          else resolve();
        };
        const stop = () => {
          audio.pause();
          settle(false);
        };
        audio.addEventListener("ended", () => {
          settle(false);
        });
        audio.addEventListener("error", () => {
          settle(true);
        });
        signal?.addEventListener("abort", stop);
        audio.src = message.clipUrl;
        // A browser returns a promise; an environment without media playback may not.
        const played = audio.play() as Promise<void> | undefined;
        if (played === undefined) settle(true);
        else
          played.catch(() => {
            settle(true);
          });
      }),
  };
}

/** Plays the server clip when a message has one, else speaks it in the browser; a clip that
 * cannot play is spoken in the browser instead (not after an abort: SKIP and DISABLE stay
 * silent). */
export function createRoutedEngine(
  clip: SpeechEngine = createClipEngine(),
  browser: SpeechEngine = createBrowserEngine(),
): SpeechEngine {
  return {
    speak: async (message, signal) => {
      if (message.clipUrl === undefined) {
        await browser.speak(message, signal);
        return;
      }
      try {
        await clip.speak(message, signal);
      } catch {
        if (signal?.aborted !== true) await browser.speak(message, signal);
      }
    },
  };
}
