// The task drawer (P0-24, FR-3.5): `?task=<id>` opens it; the recurrence picker saves
// the task's repeat rule with one PUT.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P0-24][FR-3.5] T-P0-24-16 drawer opens from the task param and saves a recurrence", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({
      project_id: project.id,
      title: "Send the weekly report",
      status: "backlog",
      version: 7,
    });
    const fake = new ProjectFake({ project, tasks: [task] });
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?task=${task.id}`,
      { viewport },
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Send the weekly report",
    });
    expect(within(drawer).getByRole("textbox", { name: "Title" })).toHaveValue(
      "Send the weekly report",
    );
    const repeats = within(drawer).getByRole("combobox", { name: "Repeats" });
    expect(repeats).toHaveValue("never");
    await user.selectOptions(repeats, "weekly");
    await user.selectOptions(
      within(drawer).getByRole("combobox", { name: "Day" }),
      "0",
    );
    expect(within(drawer).getByLabelText("Time")).toHaveValue("09:00");
    await user.click(
      within(drawer).getByRole("button", { name: "Save repeat" }),
    );

    await waitFor(() => {
      expect(
        fake.sent("PUT", `/v1/tasks/${task.id}/recurrence`).map((s) => s.body),
      ).toEqual([
        { preset: "weekly", weekday: 0, due_time: "09:00", version: 7 },
      ]);
    });
    expect(
      await within(drawer).findByText("Repeats weekly on Monday at 09:00"),
    ).toBeVisible();
    unmount();
  }
});
