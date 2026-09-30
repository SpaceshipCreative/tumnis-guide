// The card's Move menu (P0-24, UX-7): Escape closes it and gives focus back to the Move
// button, so a keyboard or screen reader user keeps their place on the board.
import { fireEvent, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-24][UX-7] Escape closes the Move menu and returns focus to Move", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({
    project,
    tasks: [
      makeTask({
        project_id: project.id,
        parent_id: null,
        title: "Send logo drafts",
        status: "backlog",
      }),
    ],
  });
  server.resetHandlers();
  server.use(...fake.handlers);
  const { user } = await renderRoute(`/projects/${project.id}?view=board`, {
    viewport: "laptop",
  });
  const backlog = await screen.findByRole("region", { name: "Backlog" });
  const card = await within(backlog).findByRole("listitem", {
    name: /Send logo drafts/,
  });
  const move = within(card).getByRole("button", { name: "Move" });
  await user.click(move);
  expect(screen.getByRole("menuitem", { name: "Today" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(move).toHaveFocus();
});

test("[P0-24][UX-7] arrows in the Move menu move focus without scrolling the page", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({
    project,
    tasks: [
      makeTask({
        project_id: project.id,
        parent_id: null,
        title: "Send logo drafts",
        status: "backlog",
      }),
    ],
  });
  server.resetHandlers();
  server.use(...fake.handlers);
  const { user } = await renderRoute(`/projects/${project.id}?view=board`, {
    viewport: "laptop",
  });
  const backlog = await screen.findByRole("region", { name: "Backlog" });
  const card = await within(backlog).findByRole("listitem", {
    name: /Send logo drafts/,
  });
  await user.click(within(card).getByRole("button", { name: "Move" }));
  const [first, second] = screen.getAllByRole("menuitem");
  if (!first || !second) throw new Error("the Move menu lists two columns");
  expect(first).toHaveFocus();

  // `fireEvent` answers false when the handler called preventDefault().
  expect(fireEvent.keyDown(first, { key: "ArrowDown" })).toBe(false);
  expect(second).toHaveFocus();
  expect(fireEvent.keyDown(second, { key: "ArrowUp" })).toBe(false);
  expect(first).toHaveFocus();
});
