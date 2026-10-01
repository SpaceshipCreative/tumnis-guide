// useVoiceMode (P4-03, FR-10.8): the focus bar's new spoken messages reach the voiceMode
// machine once each; nothing already shown when the bar loads is replayed; a server clip
// is played when there is one, and voice off says nothing.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";

import type { FocusMessageOut } from "../../api/types.gen";
import { voiceModeWith } from "../../machines/voiceMode";
import { focusMessage } from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import type { VoiceMessage } from "./types";
import { useVoiceMode } from "./useVoiceMode";

const CLIP_ID = "01950000-0000-7000-8000-00000000c11b";

function voiceSettings(values: Record<string, unknown>) {
  return http.get("*/v1/settings/voice", () =>
    HttpResponse.json({
      section: "voice",
      values,
      secrets_set: [],
      version: 1,
    }),
  );
}

/** An engine that finishes each message at once and keeps what it was asked to say. */
function recordingEngine() {
  const said: VoiceMessage[] = [];
  return {
    said,
    speak: (message: VoiceMessage) => {
      said.push(message);
      return Promise.resolve();
    },
  };
}

function setup(clipWaitMs = 3_000) {
  const engine = recordingEngine();
  const machine = voiceModeWith(engine);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    ({ messages }: { messages: FocusMessageOut[] | undefined }) => {
      useVoiceMode(messages, { machine, clipWaitMs });
    },
    {
      wrapper,
      initialProps: { messages: undefined as FocusMessageOut[] | undefined },
    },
  );
  return { engine, view };
}

test("[P4-03][FR-10.8] says each new spoken message once and never replays the loaded ones", async () => {
  server.use(voiceSettings({ enabled_levels: ["nudge"], engine: "server" }));
  const { engine, view } = setup();
  const old = focusMessage({ level: "nudge", speak: true, message: "Old" });
  view.rerender({ messages: [old] });

  const quiet = focusMessage({ level: "coach", message: "Not spoken" });
  const spoken = focusMessage({
    level: "nudge",
    speak: true,
    clip_id: CLIP_ID,
    message: "Open the proposal outline",
  });
  view.rerender({ messages: [old, quiet, spoken] });
  view.rerender({ messages: [old, quiet, spoken] });

  await waitFor(() => {
    expect(engine.said).toHaveLength(1);
  });
  expect(engine.said[0]).toEqual({
    id: spoken.id,
    text: "Open the proposal outline",
    clipUrl: expect.stringMatching(
      new RegExp(`/v1/speech/clips/${CLIP_ID}$`),
    ) as unknown,
  });
});

test("[P4-03][FR-11.7] the browser engine says a message at once, without a clip", async () => {
  server.use(voiceSettings({ enabled_levels: ["coach"], engine: "browser" }));
  const { engine, view } = setup(60_000);
  view.rerender({ messages: [] });
  const first = focusMessage({ level: "coach", speak: true, message: "One" });
  view.rerender({ messages: [first] });
  await waitFor(() => {
    expect(engine.said.map((m) => m.text)).toEqual(["One"]);
  });
  // A clip made before the switch to this device's voice is not played.
  const second = focusMessage({
    level: "coach",
    speak: true,
    clip_id: CLIP_ID,
    message: "Two",
  });
  view.rerender({ messages: [first, second] });
  await waitFor(() => {
    expect(engine.said).toEqual([
      { id: first.id, text: "One" },
      { id: second.id, text: "Two" },
    ]);
  });
});

test("[P4-03][FR-11.7] the server engine waits for the clip that lands after its message", async () => {
  server.use(voiceSettings({ enabled_levels: ["nudge"], engine: "server" }));
  const { engine, view } = setup(60_000);
  view.rerender({ messages: [] });
  const message = focusMessage({ level: "nudge", speak: true });
  view.rerender({ messages: [message] });
  view.rerender({ messages: [{ ...message, clip_id: CLIP_ID }] });
  await waitFor(() => {
    expect(engine.said).toHaveLength(1);
  });
  expect(engine.said[0]?.clipUrl).toMatch(/\/v1\/speech\/clips\//);
});

test("[P4-03][FR-10.8] voice off says nothing", async () => {
  let reads = 0;
  server.use(
    http.get("*/v1/settings/voice", () => {
      reads += 1;
      return HttpResponse.json({
        section: "voice",
        values: { enabled_levels: [] },
        secrets_set: [],
        version: 1,
      });
    }),
  );
  const { engine, view } = setup(0);
  const earlier = focusMessage({ level: "nudge", speak: true });
  view.rerender({ messages: [earlier] });
  await waitFor(() => {
    expect(reads).toBe(1);
  });
  const later = focusMessage({ level: "nudge", speak: true, clip_id: CLIP_ID });
  view.rerender({ messages: [earlier, later] });
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(engine.said).toEqual([]);
});
