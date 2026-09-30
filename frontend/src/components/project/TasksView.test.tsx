// The Tasks view's row shortcuts (P0-24, UX 7): `s` and `d` act on the focused row's own
// status action, never on a subtask nested under it.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-24][UX 7] s and d act on the focused row, not its subtasks", async () => {
  const project = makeProject({ name: "Acme site" });
  const parent = makeTask({
    project_id: project.id,
    parent_id: null,
    title: "Launch the site",
    status: "today",
    label: "human",
  });
  const sub = makeTask({
    project_id: project.id,
    parent_id: parent.id,
    title: "Check the links",
    status: "in_progress",
    label: "human",
  });
  const fake = new ProjectFake({ project, tasks: [parent, sub] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });

  // The subtask (Done) sits inside the parent's row (Start).
  expect(
    await screen.findByRole("button", { name: "Done Check the links" }),
  ).toBeVisible();
  screen.getByRole("button", { name: "Launch the site" }).focus();

  await user.keyboard("d"); // the parent has no Done: nothing happens
  await user.keyboard("s"); // the parent's own Start
  await waitFor(() => {
    expect(
      fake
        .sent("POST", `/v1/tasks/${parent.id}/status`)
        .map((s) => (s.body as { to: string }).to),
    ).toEqual(["in_progress"]);
  });
  expect(fake.sent("POST", `/v1/tasks/${sub.id}/status`)).toEqual([]);
});
