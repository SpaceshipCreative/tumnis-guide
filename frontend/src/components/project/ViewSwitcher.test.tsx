// The laptop view switcher is an ARIA tab set (P0-24, FR-2.6, UX-7): arrow keys select the
// next tab and move focus to it, and each tab controls the tabpanel that holds the view.
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

beforeEach(() => {
  localStorage.clear();
});

test("[P0-24][UX-7] arrow keys move the selection and focus between view tabs", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({
    project,
    tasks: [
      makeTask({
        project_id: project.id,
        title: "Send logo drafts",
        status: "backlog",
      }),
    ],
  });
  server.use(...fake.handlers);
  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  const tabs = await screen.findByRole("tablist", { name: "Views" });
  const tasksTab = within(tabs).getByRole("tab", { name: "Tasks" });
  const panel = screen.getByRole("tabpanel", { name: "Tasks" });
  expect(tasksTab).toHaveAttribute("aria-controls", panel.id);

  tasksTab.focus();
  await user.keyboard("{ArrowRight}");
  const boardTab = within(tabs).getByRole("tab", { name: "Board" });
  expect(boardTab).toHaveAttribute("aria-selected", "true");
  expect(boardTab).toHaveFocus();
  expect(await screen.findByRole("tabpanel", { name: "Board" })).toBe(panel);
  expect(
    await within(panel).findByRole("region", { name: "Backlog" }),
  ).toBeVisible();
});
