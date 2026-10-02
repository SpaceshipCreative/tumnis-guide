// Run unattended (P4-04, FR-4.5): the switch reads the task's queue flag and a press
// sends `PUT /v1/tasks/{id}/unattended` with the opposite; a refusal says why.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProblem, makeTask } from "../../test/factories";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { taskUnattendedHandlers } from "../../test/msw/unattended";
import { renderWithProviders } from "../../test/render";
import { RunUnattendedToggle } from "./RunUnattendedToggle";

test("[P4-04][FR-4.5] the switch queues an AI task and takes a queued one off", async () => {
  const recorder = new Recorder();
  server.use(...taskUnattendedHandlers(recorder));
  const fresh = makeTask({ label: "ai", tainted: false, status: "today" });
  const { user, unmount } = renderWithProviders(
    <RunUnattendedToggle task={fresh} />,
  );
  const off = screen.getByRole("switch", { name: "Run unattended" });
  expect(off).not.toBeChecked();
  await user.click(off);
  await waitFor(() => {
    expect(recorder.writes()).toEqual([`PUT /v1/tasks/${fresh.id}/unattended`]);
  });
  expect(recorder.sent[0]?.body).toEqual({ queued: true });
  expect(recorder.sent[0]?.idempotencyKey).toBeTruthy();
  unmount();

  const queued = makeTask({
    label: "ai",
    tainted: false,
    status: "today",
    unattended_queued_at: "2026-03-09T12:00:00Z",
  });
  const second = renderWithProviders(<RunUnattendedToggle task={queued} />);
  const on = screen.getByRole("switch", { name: "Run unattended" });
  expect(on).toBeChecked();
  await second.user.click(on);
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(2);
  });
  expect(recorder.sent[1]?.body).toEqual({ queued: false });
});

test("[P4-04][FR-4.5] a refused queue says why", async () => {
  server.use(
    http.put("*/v1/tasks/:taskId/unattended", () =>
      HttpResponse.json(
        makeProblem({
          status: 422,
          code: "not_ai",
          title: "Not an AI task",
          detail: "Only AI tasks can run unattended.",
        }),
        { status: 422 },
      ),
    ),
  );
  const task = makeTask({ label: "ai", tainted: false, status: "today" });
  const { user } = renderWithProviders(<RunUnattendedToggle task={task} />);
  await user.click(screen.getByRole("switch", { name: "Run unattended" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Only AI tasks can run unattended.",
  );
});
