// The Connections rail section shows the project's Coolify apps (P2-14, FR-12.2): each
// linked app's last deploy, and the form that links another by UUID, on phone and laptop.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P2-14][FR-12.2] the Connections section lists and links Coolify apps", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({
      name: "Dune",
      links: [{ kind: "coolify_app", value: "dune0portal0example00004" }],
    });
    const fake = new ProjectFake({
      project,
      deployApps: [
        {
          app_uuid: "dune0portal0example00004",
          name: "dune-portal",
          last: {
            status: "finished",
            commit: "0123456789abcdef",
            created_at: "2026-03-09T09:58:00Z",
            finished_at: "2026-03-09T10:03:00Z",
          },
          previews: [],
          checked_at: "2026-03-09T10:05:00Z",
          error: null,
        },
      ],
    });
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?view=tasks`,
      { viewport },
    );
    let rail: HTMLElement;
    if (viewport === "phone") {
      await user.click(await screen.findByRole("button", { name: "Context" }));
      rail = screen.getByRole("dialog", { name: "Context" });
    } else {
      rail = await screen.findByRole("complementary", { name: "Context" });
    }

    await user.click(
      await within(rail).findByRole("button", { name: /^Connections/ }),
    );
    const apps = await within(rail).findByRole("list", {
      name: "Coolify apps",
    });
    expect(
      await within(apps).findByText(/^Deployed Mar 9/),
    ).toBeInTheDocument();
    expect(
      within(apps).getByRole("listitem", { name: "dune-portal" }),
    ).toHaveTextContent("0123456");
    expect(within(rail).getByLabelText("Coolify app UUID")).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", { name: "Link app" }),
    ).toBeInTheDocument();
    unmount();
  }
});
