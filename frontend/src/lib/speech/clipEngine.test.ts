// The clip and routed engines (P4-03, FR-11.7): a server clip plays to its end; a clip that
// cannot play (gone after its TTL, refused) is spoken by the browser instead, but an abort
// (SKIP, DISABLE) never falls back to speech.
import { expect, test, vi } from "vitest";

import { createClipEngine, createRoutedEngine } from "./clipEngine";
import type { SpeechEngine, VoiceMessage } from "./types";

const MESSAGE: VoiceMessage = {
  id: "m1",
  text: "Open the proposal outline",
  clipUrl: "https://tumnis.test/v1/speech/clips/c1",
};

/** An HTMLAudioElement stand-in whose `play()` answers `played`. */
function fakeAudio(played: Promise<void>) {
  const audio = new EventTarget() as EventTarget & {
    src: string;
    play: () => Promise<void>;
    pause: () => void;
  };
  audio.src = "";
  audio.play = () => played;
  audio.pause = vi.fn();
  return audio;
}

function recorder(): SpeechEngine & { said: string[] } {
  const said: string[] = [];
  return {
    said,
    speak: (message) => {
      said.push(message.text);
      return Promise.resolve();
    },
  };
}

test("[P4-03][FR-11.7] a clip plays to its end without the browser voice", async () => {
  const audio = fakeAudio(Promise.resolve());
  const browser = recorder();
  const engine = createRoutedEngine(
    createClipEngine(() => audio as unknown as HTMLAudioElement),
    browser,
  );
  const spoken = engine.speak(MESSAGE);
  await Promise.resolve();
  expect(audio.src).toBe(MESSAGE.clipUrl);
  audio.dispatchEvent(new Event("ended"));
  await spoken;
  expect(browser.said).toEqual([]);
});

test("[P4-03][FR-11.7] a clip that cannot play is spoken by the browser", async () => {
  const audio = fakeAudio(Promise.resolve());
  const browser = recorder();
  const engine = createRoutedEngine(
    createClipEngine(() => audio as unknown as HTMLAudioElement),
    browser,
  );
  const spoken = engine.speak(MESSAGE);
  audio.dispatchEvent(new Event("error"));
  await spoken;
  expect(browser.said).toEqual([MESSAGE.text]);

  const refused = recorder();
  await createRoutedEngine(
    createClipEngine(
      () =>
        fakeAudio(
          Promise.reject(new Error("NotAllowedError")),
        ) as unknown as HTMLAudioElement,
    ),
    refused,
  ).speak(MESSAGE);
  expect(refused.said).toEqual([MESSAGE.text]);
});

test("[P4-03][FR-10.8] an aborted clip stops without falling back to speech", async () => {
  const audio = fakeAudio(new Promise<void>(() => undefined));
  const browser = recorder();
  const engine = createRoutedEngine(
    createClipEngine(() => audio as unknown as HTMLAudioElement),
    browser,
  );
  const abort = new AbortController();
  const spoken = engine.speak(MESSAGE, abort.signal);
  abort.abort();
  await spoken;
  expect(audio.pause).toHaveBeenCalled();
  expect(browser.said).toEqual([]);
});
