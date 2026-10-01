// The brief says "Saved" once a save lands (P0-24, FR-2.7): the saved brief comes back
// with a new version, and the confirmation must survive that.
import type { Editor as TiptapEditor } from "@tiptap/core";
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P0-24][FR-2.7] saving the brief shows Saved", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({
    project,
    tasks: [],
    brief: "Logo refresh for Acme",
  });
  server.resetHandlers();
  server.use(...fake.handlers);

  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  const rail = await screen.findByRole("complementary", { name: "Context" });
  await user.click(await within(rail).findByRole("button", { name: /^Brief/ }));
  const text = await within(rail).findByRole("textbox", { name: "Brief" });
  // The brief is in the note editor (P1-17, Scott decision 39). jsdom has no layout for
  // ProseMirror to read typed DOM text, so clearing and typing go through the editor's
  // own transactions, as a keystroke would.
  const editor = (text as HTMLElement & { editor: TiptapEditor }).editor;
  editor
    .chain()
    .selectAll()
    .deleteSelection()
    .insertContent("Logo and site refresh")
    .run();
  await user.click(within(rail).getByRole("button", { name: "Save brief" }));

  expect(await within(rail).findByText("Saved")).toBeVisible();
  expect(
    within(rail).getByRole("textbox", { name: "Brief" }),
  ).toHaveTextContent("Logo and site refresh");
});
