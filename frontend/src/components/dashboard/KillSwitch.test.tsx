// The kill switch (P2-09, SAF-4): one control pauses every agent. Pausing asks for a
// reason in a confirm dialog (`POST /v1/agents/pause`), a banner says the agents are
// paused and why, and Resume (`POST /v1/agents/resume`) is the human act that lets them
// run again. The pause state is read from `GET /v1/agents/pause`.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";

const PAUSE_ID = "01950000-0000-7000-8000-000000000b01";

interface KillSwitchModule {
  KillSwitch: () => React.JSX.Element;
}

// Loaded at run time, so this file compiles before KillSwitch.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

function openPause(reason: string) {
  return {
    pause_id: PAUSE_ID,
    scope: "workspace",
    project_id: null,
    reason,
    paused_at: "2026-03-09T12:00:00Z",
    paused_by: "user:01950000-0000-7000-8000-000000000b02",
  };
}

test("[P2-09][SAF-4] pause needs a reason and shows paused state", async () => {
  const { KillSwitch } = await load<KillSwitchModule>("./KillSwitch");
  let reason: string | null = null;
  const recorder = new Recorder();
  server.use(
    http.get("*/v1/agents/pause", () =>
      HttpResponse.json({
        schema_version: 1,
        workspace: reason === null ? null : openPause(reason),
        projects: [],
      }),
    ),
    http.post("*/v1/agents/pause", async ({ request }) => {
      const sent = await recorder.record(request);
      reason = (sent.body as { reason: string }).reason;
      return HttpResponse.json(
        {
          schema_version: 1,
          pause_id: PAUSE_ID,
          cancelled_runs: 2,
          held_runs: 1,
        },
        { status: 202 },
      );
    }),
    http.post("*/v1/agents/resume", async ({ request }) => {
      await recorder.record(request);
      reason = null;
      return HttpResponse.json(
        { schema_version: 1, pause_id: PAUSE_ID, released_runs: 1 },
        { status: 202 },
      );
    }),
  );
  const { user } = renderWithProviders(<KillSwitch />);

  // Running: one control, no banner.
  const pauseAll = await screen.findByRole("button", {
    name: "Pause all agents",
  });
  expect(screen.queryByRole("status", { name: "Agents paused" })).toBeNull();

  // The confirm dialog: Pause stays disabled until a reason is typed.
  await user.click(pauseAll);
  const dialog = screen.getByRole("dialog", { name: "Pause all agents" });
  const confirm = within(dialog).getByRole("button", { name: "Pause" });
  expect(confirm).toBeDisabled();
  const field = within(dialog).getByRole("textbox", { name: "Reason" });
  await user.type(field, "   ");
  expect(confirm).toBeDisabled();
  await user.clear(field);
  await user.type(field, "The agent is looping");
  expect(confirm).toBeEnabled();
  await user.click(confirm);

  await waitFor(() => {
    expect(recorder.writes()).toEqual(["POST /v1/agents/pause"]);
  });
  const pause = recorder.sent[0];
  expect(pause?.body).toEqual({
    scope: "workspace",
    reason: "The agent is looping",
  });
  expect(pause?.idempotencyKey).toBeTruthy();

  // Paused: a banner with the reason, and Resume in place of the pause control.
  const banner = await screen.findByRole("status", { name: "Agents paused" });
  expect(banner).toHaveTextContent("The agent is looping");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Pause all agents" })).toBeNull();

  await user.click(
    within(banner).getByRole("button", { name: "Resume agents" }),
  );
  await waitFor(() => {
    expect(recorder.writes()).toEqual([
      "POST /v1/agents/pause",
      "POST /v1/agents/resume",
    ]);
  });
  expect(recorder.sent[1]?.body).toMatchObject({ scope: "workspace" });
  expect(recorder.sent[1]?.idempotencyKey).toBeTruthy();
  expect(
    await screen.findByRole("button", { name: "Pause all agents" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "Agents paused" })).toBeNull();
});
