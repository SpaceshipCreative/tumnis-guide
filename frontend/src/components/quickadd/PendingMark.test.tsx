// Pending marks (P0-25, FR-3.10): a capture shows at once in the Tasks view with a
// "Waiting to sync" mark; the mark stays while the device is offline and while the
// request is out, and goes once the server has the task, which then shows once.
import { act, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-25][FR-3.10] T-P0-25-11 pending marks show until synced", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({ project });
  let release: () => void = () => undefined;
  fake.createGate = new Promise((resolve) => {
    release = resolve;
  });
  server.use(...fake.handlers);

  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "phone",
  });
  const upNext = await screen.findByRole("region", { name: "Up next" });

  // Offline: the row shows with its mark and nothing is sent.
  act(() => {
    window.dispatchEvent(new Event("offline"));
  });
  await user.type(
    screen.getByRole("textbox", { name: "New task" }),
    "Offline one{Enter}",
  );
  const row = await within(upNext).findByRole("listitem");
  expect(row).toHaveTextContent("Offline one");
  expect(within(row).getByTestId("pending-mark")).toHaveTextContent(
    "Waiting to sync",
  );
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(fake.sent("POST", "/v1/tasks")).toEqual([]);

  // Back online: the request goes; the mark stays until the server answers.
  act(() => {
    window.dispatchEvent(new Event("online"));
  });
  await waitFor(() => {
    expect(fake.sent("POST", "/v1/tasks")).toHaveLength(1);
  });
  expect(screen.getAllByTestId("pending-mark")).toHaveLength(1);

  release();
  await waitFor(() => {
    expect(screen.queryAllByTestId("pending-mark")).toHaveLength(0);
  });
  await waitFor(() => {
    expect(within(upNext).getAllByText("Offline one")).toHaveLength(1);
  });
});
