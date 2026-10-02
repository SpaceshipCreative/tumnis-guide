// APP-F04 (final application test, a11y): after "Add comment" focus fell to <body> (the
// focused submit button was disabled while the comment saved), and the first Escape then
// did not close the drawer, whose Escape handler only heard keys from inside it. Focus now
// goes back to the comment field once the comment is added, and Escape closes the open
// drawer while focus is on <body>. Laptop and phone.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

const VIEWPORTS = ["laptop", "phone"] as const;

async function openDrawer(viewport: (typeof VIEWPORTS)[number]) {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send logo drafts to Acme",
    status: "today",
    label: "human",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const rendered = await renderRoute(
    `/projects/${project.id}?view=tasks&task=${task.id}`,
    { viewport },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send logo drafts to Acme",
  });
  return { ...rendered, drawer, fake, task };
}

test("[P0-24][UX 11] APP-F04 focus goes back to the comment field after Add comment", async () => {
  for (const viewport of VIEWPORTS) {
    const { user, drawer, unmount } = await openDrawer(viewport);
    const field = within(drawer).getByRole("textbox", { name: "Comment" });
    await user.type(field, "Drafts attached");
    await user.click(
      within(drawer).getByRole("button", { name: "Add comment" }),
    );

    expect(
      await within(drawer).findByText("Drafts attached"),
      viewport,
    ).toBeVisible();
    await waitFor(() => {
      expect(field, viewport).toHaveFocus();
    });
    expect(field, viewport).toHaveValue("");

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Send logo drafts to Acme" }),
        viewport,
      ).toBeNull();
    });
    unmount();
  }
});

test("[P0-24][UX 11] APP-F04 Escape closes the open drawer while focus is on the page body", async () => {
  for (const viewport of VIEWPORTS) {
    const { user, drawer, unmount } = await openDrawer(viewport);
    within(drawer).getByRole("button", { name: "Add comment" }).focus();
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement, viewport).toBe(document.body);

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Send logo drafts to Acme" }),
        viewport,
      ).toBeNull();
    });
    unmount();
  }
});
