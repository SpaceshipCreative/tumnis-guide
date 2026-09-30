// The drawer's pull request section (P2-13, FR-12.1): the linked pull requests with their
// status, and a field that links another and says why a link was refused.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P2-13][FR-12.1] drawer lists linked pull requests and links another", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({ project_id: project.id, title: "Ship the form" });
    const fake = new ProjectFake({ project, tasks: [task] });
    fake.pullRequests.set(task.id, [
      {
        artifact_id: crypto.randomUUID(),
        url: "https://github.com/acme-example/site/pull/42",
        repo: "acme-example/site",
        number: 42,
        title: "Add the booking form",
        state: "open",
        draft: false,
        checks: "red",
        review: "review_required",
        checked_at: "2026-03-09T12:00:00Z",
      },
    ]);
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?task=${task.id}`,
      { viewport },
    );
    const drawer = await screen.findByRole("dialog", { name: "Ship the form" });
    expect(
      await within(drawer).findByRole("link", {
        name: "Pull request acme-example/site#42, Add the booking form: open, checks failing, review required",
      }),
    ).toHaveAttribute("href", "https://github.com/acme-example/site/pull/42");
    expect(within(drawer).getByText("Checks failing")).toBeVisible();

    const field = within(drawer).getByRole("textbox", {
      name: "Pull request link",
    });
    fake.refuseLink = "repo_not_allowed";
    await user.type(field, "https://github.com/brio-example/site/pull/7");
    await user.click(
      within(drawer).getByRole("button", { name: "Link pull request" }),
    );
    expect(await within(drawer).findByRole("alert")).toHaveTextContent(
      "not on the allow-list",
    );

    fake.refuseLink = null;
    await user.clear(field);
    await user.type(field, "https://github.com/acme-example/site/pull/43");
    await user.click(
      within(drawer).getByRole("button", { name: "Link pull request" }),
    );
    await waitFor(() => {
      expect(
        fake
          .sent("POST", `/v1/tasks/${task.id}/pull-requests`)
          .map((s) => s.body),
      ).toEqual([
        { url: "https://github.com/brio-example/site/pull/7" },
        { url: "https://github.com/acme-example/site/pull/43" },
      ]);
    });
    expect(
      await within(drawer).findByRole("link", {
        name: "Pull request acme-example/site#43: status not read yet",
      }),
    ).toBeVisible();
    expect(within(drawer).queryByRole("alert")).toBeNull();
    unmount();
  }
});
