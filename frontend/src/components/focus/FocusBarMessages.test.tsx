// The focus bar's messages and its Start action (P2-15, FR-10.2, FR-10.9): every message
// of the day with the level and rule that produced it, the latest in view and the rest
// behind "Earlier today"; a block's message offers Start, which moves its task to In
// progress with the version read.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import {
  focusCurrent,
  focusMessage,
  focusSession,
  quietFocus,
} from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

test("[P2-15][FR-10.9] each message shows its attribution; a block offers Start", async () => {
  const task = makeTask({
    title: "Write proposal",
    status: "today",
    version: 7,
  });
  const blockStart = focusMessage({
    kind: "block_start",
    task_id: task.id,
    level: "nudge",
    rule: "Nudge · block_start",
    message: "Write proposal starts now. First action: outline",
    fired_at: "2026-03-10T14:00:00Z",
  });
  const notStarted = focusMessage({
    kind: "not_started",
    task_id: task.id,
    level: "nudge",
    rule: "Nudge · not_started",
    message: "Write proposal has not started yet",
  });
  const recorder = new Recorder();
  server.use(
    focusCurrent({
      ...quietFocus(),
      level: "nudge",
      workspace_level: "nudge",
      messages: [blockStart, notStarted],
    }),
    http.get("*/v1/tasks/:taskId", () => HttpResponse.json(task)),
    http.post("*/v1/tasks/:taskId/status", async ({ request }) => {
      await recorder.record(request);
      return HttpResponse.json({ ...task, status: "in_progress", version: 8 });
    }),
  );
  const view = await renderRoute("/", { viewport: "phone" });

  const bar = await screen.findByRole("region", { name: "Focus" });
  const messages = within(bar).getAllByTestId("focus-message");
  expect(messages).toHaveLength(2);
  expect(
    messages.map((m) => within(m).getByTestId("focus-attribution").textContent),
  ).toEqual(["Nudge · block_start", "Nudge · not_started"]);
  expect(within(bar).getByTestId("focus-level")).toHaveTextContent("Nudge");
  expect(within(bar).getByText("Earlier today (1)")).toBeInTheDocument();

  const start = within(bar).getByRole("button", { name: "Start" });
  await waitFor(() => {
    expect(start).toBeEnabled();
  });
  await view.user.click(start);
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  expect(recorder.sent[0]?.path).toBe(`/v1/tasks/${task.id}/status`);
  expect(recorder.sent[0]?.body).toEqual({ to: "in_progress", version: 7 });
});

test("[P2-15][FR-10.4] a reply that fails reopens the check-in and says so", async () => {
  const session = focusSession("Write proposal", 30);
  const checkIn = focusMessage({ task_id: session.task_id });
  let posts = 0;
  server.use(
    focusCurrent({
      ...quietFocus(),
      level: "coach",
      workspace_level: "coach",
      session,
      messages: [checkIn],
    }),
    http.post("*/v1/focus/respond", () => {
      posts += 1;
      return HttpResponse.json(
        { type: "about:blank", title: "Unavailable", status: 503, code: "x" },
        { status: 503 },
      );
    }),
  );
  const view = await renderRoute("/", { viewport: "phone" });

  const bar = await screen.findByRole("region", { name: "Focus" });
  await view.user.click(within(bar).getByRole("button", { name: "Snooze" }));

  expect(await within(bar).findByRole("alert")).toHaveTextContent(
    "Your answer was not saved",
  );
  expect(
    await within(bar).findByRole("button", { name: "Snooze" }),
  ).toBeInTheDocument();
  expect(posts).toBe(1);
});

test("[P2-15][FR-10.9] at Quiet with nothing today the loaded page has no focus bar", async () => {
  server.use(focusCurrent(quietFocus()));
  await renderRoute("/", { viewport: "laptop" });

  // The dashboard's level chip reads the same focus state: once it shows, the bar has
  // had its answer too.
  expect(await screen.findByText("Focus: Quiet")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Focus" })).toBeNull();
});
