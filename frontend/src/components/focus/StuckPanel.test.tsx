// The stuck panel (P4-02, FR-10.5): after "Stuck", the focus bar says the project agent is
// working on a first step, then shows the step when the live socket says focus changed:
// the subtask with its minutes, the agent's report, or the fallback (the task's first
// action with a 10-minute timer).
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

import type { FocusCurrentOut } from "../../api/types.gen";
import { connectLive } from "../../lib/ws";
import { FakeSocket } from "../../test/fakeSocket";
import { focusMessage, focusSession, quietFocus } from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  FakeSocket.reset();
});

/** `GET /v1/focus/current` with a stuck request's `next_step`. */
function withNextStep(
  body: FocusCurrentOut,
  nextStep: Record<string, unknown>,
): FocusCurrentOut {
  return { ...body, next_step: nextStep } as unknown as FocusCurrentOut;
}

test.fails("[P4-02][FR-10.5] shows waiting then the step", async () => {
  vi.stubGlobal("WebSocket", FakeSocket);
  const session = focusSession("Send the March invoice", 30);
  const stuck = focusMessage({
    kind: "stuck",
    task_id: session.task_id,
    rule: "Coach · stuck",
    message:
      "Stuck on Send the March invoice. Name the smallest next step, or ask for help.",
    response: null,
  });
  const base: FocusCurrentOut = {
    ...quietFocus(),
    level: "coach",
    workspace_level: "coach",
    session,
    messages: [stuck],
  };
  const request = {
    focus_event_id: stuck.id,
    task_id: session.task_id,
    run_id: crypto.randomUUID(),
    requested_at: new Date().toISOString(),
    first_action: "Open the invoice template",
    timer_minutes: 10,
    fallback_at: null,
    summary: null,
  };
  let current = withNextStep(base, {
    ...request,
    state: "working",
    step: null,
  });
  server.use(http.get("*/v1/focus/current", () => HttpResponse.json(current)));

  const { queryClient } = await renderRoute("/", { viewport: "phone" });
  const stop = connectLive(queryClient, "ws://localhost/ws");
  try {
    FakeSocket.latest().open();
    const bar = await screen.findByRole("region", { name: "Focus" });
    const panel = await within(bar).findByRole("status", { name: "Next step" });
    expect(panel).toHaveTextContent("Working on a first step…");

    current = withNextStep(base, {
      ...request,
      state: "split",
      step: {
        task_id: crypto.randomUUID(),
        title: "Find last month's invoice to copy",
        label: "human",
        estimate_minutes: 8,
      },
    });
    FakeSocket.latest().receive({ entity: "focus", id: stuck.id });

    expect(
      await within(panel).findByText("Find last month's invoice to copy"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("8 min")).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("Working on a first step…");
  } finally {
    stop();
  }
});
