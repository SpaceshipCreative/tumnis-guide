// The brief says "Saved" once a save lands (P0-24, FR-2.7): the saved brief comes back
// with a new version, and the confirmation must survive that.
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
  await user.clear(text);
  await user.type(text, "Logo and site refresh");
  await user.click(within(rail).getByRole("button", { name: "Save brief" }));

  expect(await within(rail).findByText("Saved")).toBeVisible();
  expect(within(rail).getByRole("textbox", { name: "Brief" })).toHaveValue(
    "Logo and site refresh",
  );
});
