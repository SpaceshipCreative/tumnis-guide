// Settings > Voice (P4-03, FR-10.8, FR-11.7): off by default with this device's voice;
// ticking a level unlocks speech inside the click (iOS) and Save sends the section.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

import type { SettingSectionOut } from "../../../api/types.gen";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders } from "../../../test/render";
import { VoiceSection } from "./VoiceSection";

afterEach(() => {
  vi.unstubAllGlobals();
});

function voiceHandlers(recorder: Recorder, stored: SettingSectionOut) {
  let current = stored;
  return [
    http.get("*/v1/settings/voice", () => HttpResponse.json(current)),
    http.put("*/v1/settings/voice", async ({ request }) => {
      const sent = await recorder.record(request);
      const body = sent.body as { values: Record<string, unknown> };
      current = {
        section: "voice",
        values: { ...current.values, ...body.values },
        secrets_set: [],
        version: (current.version ?? 0) + 1,
      };
      return HttpResponse.json(current);
    }),
  ];
}

test("[P4-03][FR-11.7] voice is off by default and saves the chosen levels and engine", async () => {
  const speak = vi.fn();
  vi.stubGlobal("speechSynthesis", { speak, cancel: vi.fn() });
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      constructor(readonly text: string) {}
    },
  );
  const recorder = new Recorder();
  server.use(
    ...voiceHandlers(recorder, {
      section: "voice",
      values: {},
      secrets_set: [],
      version: null,
    }),
  );
  const { user } = renderWithProviders(<VoiceSection />);

  const nudge = await screen.findByRole("checkbox", { name: "Nudge" });
  expect(nudge).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Coach" })).not.toBeChecked();
  expect(
    screen.getByRole("radio", { name: "This device's voice" }),
  ).toBeChecked();
  expect(
    screen.queryByRole("checkbox", { name: "Allow hosted speech" }),
  ).not.toBeInTheDocument();

  await user.click(nudge);
  expect(speak).toHaveBeenCalledTimes(1); // the iOS unlock, inside the click
  await user.click(screen.getByRole("radio", { name: "The server's voice" }));
  expect(screen.getByRole("radio", { name: "Piper (local)" })).toBeChecked();
  expect(
    screen.getByRole("checkbox", { name: "Allow hosted speech" }),
  ).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => {
    expect(recorder.writes()).toEqual(["PUT /v1/settings/voice"]);
  });
  expect(recorder.sent.at(-1)?.body).toEqual({
    values: {
      enabled_levels: ["nudge"],
      engine: "server",
      server_provider: "piper",
      hosted_allowed: false,
    },
    version: null,
  });
  expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
});
