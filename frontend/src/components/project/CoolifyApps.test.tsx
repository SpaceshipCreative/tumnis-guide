// Linking Coolify applications to a project by UUID (P2-14, FR-12.2): one versioned
// PATCH of the project's links that keeps its other links, at phone and laptop widths.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { ProjectLinkIn } from "../../api/types.gen";
import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderWithProviders, type Viewport } from "../../test/render";
import { CoolifyApps } from "./DeployStatus";

const TZ = "America/New_York";
const REPO: ProjectLinkIn = { kind: "repo", value: "https://example.org/r" };
const LINKED: ProjectLinkIn = {
  kind: "coolify_app",
  value: "dune0portal0example00004",
};

for (const viewport of ["phone", "laptop"] as const satisfies Viewport[]) {
  test(`[P2-14][FR-12.2] links and unlinks apps by UUID (${viewport})`, async () => {
    const project = makeProject({ version: 3, links: [REPO, LINKED] });
    const patches: unknown[] = [];
    server.use(
      http.patch(`/v1/projects/${project.id}`, async ({ request }) => {
        patches.push(await request.json());
        return HttpResponse.json(project);
      }),
    );
    const { user } = renderWithProviders(
      <CoolifyApps
        project={project}
        apps={[
          {
            app_uuid: LINKED.value,
            name: "dune-portal",
            last: null,
            previews: [],
          },
        ]}
        timeZone={TZ}
      />,
      { viewport },
    );

    const list = screen.getByRole("list", { name: "Coolify apps" });
    expect(
      within(list).getByRole("listitem", { name: "dune-portal" }),
    ).toHaveTextContent("No deploys yet");

    const input = screen.getByLabelText("Coolify app UUID");
    await user.type(input, "Not a UUID!");
    await user.click(screen.getByRole("button", { name: "Link app" }));
    expect(screen.getByRole("alert")).toHaveTextContent("letters and digits");
    expect(patches).toHaveLength(0);

    await user.clear(input);
    await user.type(input, "cove0web0example00000003");
    await user.click(screen.getByRole("button", { name: "Link app" }));
    await waitFor(() => {
      expect(patches).toHaveLength(1);
    });
    expect(patches[0]).toEqual({
      links: [
        REPO,
        LINKED,
        { kind: "coolify_app", value: "cove0web0example00000003" },
      ],
      version: 3,
    });

    await user.click(
      screen.getByRole("button", { name: "Unlink dune-portal" }),
    );
    await waitFor(() => {
      expect(patches).toHaveLength(2);
    });
    expect(patches[1]).toEqual({ links: [REPO], version: 3 });
  });
}

test("[P2-14][FR-12.2] no linked apps says so", () => {
  renderWithProviders(
    <CoolifyApps
      project={makeProject({ links: [REPO] })}
      apps={[]}
      timeZone={TZ}
    />,
    { viewport: "phone" },
  );
  expect(screen.getByText("No Coolify apps linked.")).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Coolify apps" })).toBeNull();
});
