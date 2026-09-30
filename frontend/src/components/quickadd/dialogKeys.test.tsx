// Quick add and search are aria-modal (P0-25, NFR-6): Tab stays inside them, and an
// Escape that ends an IME composition does not close them.
import { fireEvent, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { accountHandlers, Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

test("quick add keeps Tab inside and ignores Escape while composing", async () => {
  const project = makeProject({ name: "Acme site" });
  server.use(
    ...new ProjectFake({ project }).handlers,
    ...accountHandlers(new Recorder()),
  );
  const { user } = await renderRoute("/", { viewport: "laptop" });
  await user.keyboard("/");
  const dialog = await screen.findByRole("dialog", { name: "Quick add" });
  const title = within(dialog).getByRole("textbox", { name: "Title" });
  const add = within(dialog).getByRole("button", { name: "Add" });

  add.focus();
  await user.tab();
  expect(title).toHaveFocus();
  await user.tab({ shift: true });
  expect(add).toHaveFocus();

  fireEvent.keyDown(title, { key: "Escape", isComposing: true });
  expect(screen.getByRole("dialog", { name: "Quick add" })).toBeVisible();
});

test("search keeps Tab inside and ignores Escape while composing", async () => {
  const project = makeProject({ name: "Acme site" });
  server.use(
    ...new ProjectFake({ project }).handlers,
    ...accountHandlers(new Recorder()),
  );
  const { user } = await renderRoute("/", { viewport: "laptop" });
  await user.keyboard("{Control>}k{/Control}");
  const palette = await screen.findByRole("dialog", { name: "Search" });
  const box = within(palette).getByRole("searchbox", { name: "Search" });

  expect(box).toHaveFocus();
  await user.tab();
  expect(box).toHaveFocus();
  await user.tab({ shift: true });
  expect(box).toHaveFocus();

  fireEvent.keyDown(box, { key: "Escape", isComposing: true });
  expect(screen.getByRole("dialog", { name: "Search" })).toBeVisible();
});
