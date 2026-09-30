// The taint mark (P2-08, SAF-1, design decision 14): a task made from outside content
// says so, in words, on its board card and in its drawer; a task that is not tainted
// never shows the mark.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

const MARK = "From outside content";

test.fails("[P2-08][SAF-1] shows the mark on card and drawer", async () => {
  const project = makeProject({ name: "Acme site" });
  const outside = makeTask({
    project_id: project.id,
    parent_id: null,
    title: "Reply to the Acme invoice email",
    status: "backlog",
    tainted: true,
  });
  const inside = makeTask({
    project_id: project.id,
    parent_id: null,
    title: "Tidy the Acme footer",
    status: "backlog",
    tainted: false,
  });
  const fake = new ProjectFake({ project, tasks: [outside, inside] });
  server.resetHandlers();
  server.use(...fake.handlers);

  const board = await renderRoute(`/projects/${project.id}?view=board`, {
    viewport: "laptop",
  });
  const backlog = await screen.findByRole("region", { name: "Backlog" });
  const tainted = await within(backlog).findByRole("listitem", {
    name: /Reply to the Acme invoice email/,
  });
  const clean = within(backlog).getByRole("listitem", {
    name: /Tidy the Acme footer/,
  });
  expect(within(tainted).getByText(MARK)).toBeVisible();
  expect(within(clean).queryByText(MARK)).toBeNull();
  board.unmount();

  for (const viewport of ["phone", "laptop"] as const) {
    for (const [task, marked] of [
      [outside, true],
      [inside, false],
    ] as const) {
      const { unmount } = await renderRoute(
        `/projects/${project.id}?task=${task.id}`,
        { viewport },
      );
      const drawer = await screen.findByRole("dialog", { name: task.title });
      if (marked) {
        expect(within(drawer).getByText(MARK)).toBeVisible();
      } else {
        expect(within(drawer).queryByText(MARK)).toBeNull();
      }
      unmount();
    }
  }
});
