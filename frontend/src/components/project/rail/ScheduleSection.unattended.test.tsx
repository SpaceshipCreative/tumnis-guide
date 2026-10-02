// The Schedule rail's unattended window (P4-04, FR-4.5): the window in force for the
// project and where it comes from; the project sets its own (a PUT naming it) or goes back
// to the workspace's (`window: null`).
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import {
  unattendedWindow,
  unattendedWindowHandlers,
  WEEKNIGHTS,
} from "../../../test/msw/unattended";
import { renderWithProviders } from "../../../test/render";
import { ScheduleSection } from "./ScheduleSection";

const PROJECT = "0199aa00-0000-7000-8000-0000000000a1";

test("[P4-04][FR-4.5] Schedule shows the window in force and sets the project's own", async () => {
  const recorder = new Recorder();
  server.use(
    http.get("*/v1/recurrence", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
    ...unattendedWindowHandlers(
      recorder,
      unattendedWindow({
        project_id: PROJECT,
        window: WEEKNIGHTS,
        source: "workspace",
        version: null,
      }),
      WEEKNIGHTS,
    ),
  );
  const { user } = renderWithProviders(
    <ScheduleSection
      projectId={PROJECT}
      open
      onToggle={() => undefined}
      onOpenTask={() => undefined}
    />,
    { viewport: "phone" },
  );

  const section = await screen.findByRole("region", {
    name: "Unattended runs",
  });
  expect(
    await within(section).findByText(
      "Uses the workspace window: Mon, Tue, Wed, Thu, Fri, 22:00 to 06:00 (overnight).",
    ),
  ).toBeInTheDocument();
  // The form starts from the window in force; there is no own window to drop yet.
  expect(within(section).getByLabelText("Starts at")).toHaveValue("22:00");
  expect(
    within(section).queryByRole("button", { name: "Use the workspace window" }),
  ).toBeNull();

  fireEvent.change(within(section).getByLabelText("Ends at"), {
    target: { value: "05:00" },
  });
  await user.click(within(section).getByRole("button", { name: "Save" }));
  await waitFor(() => {
    expect(recorder.writes()).toEqual(["PUT /v1/unattended/window"]);
  });
  expect(recorder.sent[0]?.body).toEqual({
    project_id: PROJECT,
    window: {
      weekdays: [0, 1, 2, 3, 4],
      start_local: "22:00:00",
      end_local: "05:00:00",
    },
    version: null,
  });
  expect(
    await within(section).findByText(
      "This project's own window: Mon, Tue, Wed, Thu, Fri, 22:00 to 05:00 (overnight).",
    ),
  ).toBeInTheDocument();

  await user.click(
    within(section).getByRole("button", { name: "Use the workspace window" }),
  );
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(2);
  });
  expect(recorder.sent[1]?.body).toEqual({
    project_id: PROJECT,
    window: null,
    version: 1,
  });
  expect(
    await within(section).findByText(
      "Uses the workspace window: Mon, Tue, Wed, Thu, Fri, 22:00 to 06:00 (overnight).",
    ),
  ).toBeInTheDocument();
});

test("[P4-04][FR-4.5] Schedule says when no window applies", async () => {
  server.use(
    http.get("*/v1/recurrence", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  );
  renderWithProviders(
    <ScheduleSection
      projectId={PROJECT}
      open
      onToggle={() => undefined}
      onOpenTask={() => undefined}
    />,
  );
  expect(
    await screen.findByText(
      "No window: this project's AI tasks do not run unattended.",
    ),
  ).toBeInTheDocument();
});
