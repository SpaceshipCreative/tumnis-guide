// The phone task drawer is a modal sheet (P0-24, FR-3.5, UX 11). APP-14 (application
// test): it said aria-modal but Tab walked out of it, through the Quick add button to the
// page behind. APP-15: the Quick add button sat on top of the open sheet and covered its
// comment box. Tab and Shift+Tab now wrap inside the sheet, closing it gives focus back to
// the row that opened it, and Quick add is hidden while a modal sheet is open.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

async function openPhoneDrawer() {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Fix footer link",
    status: "today",
    label: "human",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const rendered = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "phone",
  });
  const row = await screen.findByRole("button", { name: "Fix footer link" });
  return { ...rendered, row, project };
}

test("[P0-24][UX 11] APP-14 the phone task drawer keeps Tab inside and gives focus back", async () => {
  const { user, row } = await openPhoneDrawer();
  await user.click(row);
  const drawer = await screen.findByRole("dialog", { name: "Fix footer link" });
  expect(drawer).toHaveAttribute("aria-modal", "true");
  const close = within(drawer).getByRole("button", { name: "Close" });

  // Twenty-five Tabs (the application test's walk) never leave the sheet.
  for (let i = 0; i < 25; i += 1) {
    await user.tab();
    expect(drawer).toContainElement(document.activeElement as HTMLElement);
  }
  // Shift+Tab from the first control wraps to the last one, still inside.
  close.focus();
  await user.tab({ shift: true });
  expect(document.activeElement).not.toBe(close);
  expect(drawer).toContainElement(document.activeElement as HTMLElement);
  await user.tab();
  expect(close).toHaveFocus();

  await user.click(close);
  await waitFor(() => {
    expect(
      screen.queryByRole("dialog", { name: "Fix footer link" }),
    ).toBeNull();
  });
  expect(screen.getByRole("button", { name: "Fix footer link" })).toHaveFocus();
});

test("[P0-24][UX 11] APP-15 Quick add is hidden while the phone task drawer is open", async () => {
  const { user, row } = await openPhoneDrawer();
  expect(screen.getByRole("button", { name: "Quick add" })).toBeVisible();

  await user.click(row);
  const drawer = await screen.findByRole("dialog", { name: "Fix footer link" });
  expect(screen.queryByRole("button", { name: "Quick add" })).toBeNull();

  await user.click(within(drawer).getByRole("button", { name: "Close" }));
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "Quick add" })).toBeVisible();
  });
});

test("[P0-24][UX 11] APP-15 Quick add is hidden while the phone Context sheet is open", async () => {
  const { user } = await openPhoneDrawer();
  await user.click(screen.getByRole("button", { name: "Context" }));
  const sheet = await screen.findByRole("dialog", { name: "Context" });
  expect(screen.queryByRole("button", { name: "Quick add" })).toBeNull();

  // The Context sheet keeps Tab inside too (APP-14's defect, same sheet kind).
  for (let i = 0; i < 25; i += 1) {
    await user.tab();
    expect(sheet).toContainElement(document.activeElement as HTMLElement);
  }

  await user.click(within(sheet).getByRole("button", { name: "Close" }));
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "Quick add" })).toBeVisible();
  });
});
