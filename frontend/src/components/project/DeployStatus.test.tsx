// Coolify status on project cards (P2-14, FR-12.2): each linked application's last
// deployment (status in words, time in the workspace timezone, short commit) and the
// preview links of open pull requests, at phone and laptop widths.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { renderWithRouter, type Viewport } from "../../test/render";
import { ProjectCard } from "../dashboard/ProjectCard";
import { DeployStatus, type AppDeployStatus } from "./DeployStatus";

const TZ = "America/New_York"; // EDT (UTC-4) on 2026-03-09
const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];

const APPS: readonly AppDeployStatus[] = [
  {
    app_uuid: "dune0portal0example00004",
    name: "dune-portal",
    last: {
      status: "finished",
      commit: "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d",
      created_at: "2026-03-09T09:00:00Z",
      finished_at: "2026-03-09T09:03:30Z",
    },
    previews: [
      {
        pull_request_id: 9,
        url: "https://9.portal.example.org",
        status: "finished",
        commit: "99887766554433221100ffeeddccbbaa99887766",
        finished_at: "2026-03-08T12:03:00Z",
      },
      {
        pull_request_id: 14,
        url: "https://14.portal.example.org",
        status: "in_progress",
        commit: "aa11bb22cc33dd44ee55ff6677889900aabbccdd",
        finished_at: null,
      },
    ],
    checked_at: "2026-03-09T11:55:00Z",
    error: null,
  },
  {
    app_uuid: "brio0api0example00000002",
    name: "brio-api",
    last: {
      status: "failed",
      commit: "c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7",
      created_at: "2026-03-09T10:05:44Z",
      finished_at: "2026-03-09T10:07:02Z",
    },
    previews: [],
    checked_at: "2026-03-09T11:55:00Z",
    error: "unavailable",
  },
  {
    app_uuid: "cove0web0example00000003",
    name: "cove-web",
    last: {
      status: "in_progress",
      commit: "HEAD",
      created_at: "2026-03-09T11:58:30Z",
      finished_at: null,
    },
    previews: [],
    checked_at: "2026-03-09T11:59:00Z",
    error: null,
  },
  {
    app_uuid: "eden0docs0example0000005",
    name: null,
    last: null,
    previews: [],
    checked_at: null,
    error: null,
  },
];

function expectStatus(scope: HTMLElement): void {
  const portal = within(scope).getByRole("listitem", { name: "dune-portal" });
  expect(within(portal).getByTestId("deploy-chip")).toHaveTextContent(
    "Deployed Mar 9, 5:03 AM",
  );
  expect(portal).toHaveTextContent("f0e1d2c");
  expect(portal).not.toHaveTextContent("f0e1d2c3");
  for (const [pr, url] of [
    [9, "https://9.portal.example.org"],
    [14, "https://14.portal.example.org"],
  ] as const) {
    const link = within(portal).getByRole("link", {
      name: `PR #${String(pr)} preview`,
    });
    expect(link).toHaveAttribute("href", url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  }

  const api = within(scope).getByRole("listitem", { name: "brio-api" });
  expect(within(api).getByTestId("deploy-chip")).toHaveTextContent(
    "Deploy failed Mar 9, 6:07 AM",
  );
  expect(api).toHaveTextContent("c4d5e6f");
  expect(api).toHaveTextContent("Status out of date");
  expect(within(api).queryByRole("link")).toBeNull();

  const web = within(scope).getByRole("listitem", { name: "cove-web" });
  expect(within(web).getByTestId("deploy-chip")).toHaveTextContent(
    "Deploying since Mar 9, 7:58 AM",
  );
  expect(web).not.toHaveTextContent("Status out of date");

  // No name yet (not polled): the UUID stands in; no deployment yet says so.
  const docs = within(scope).getByRole("listitem", {
    name: "eden0docs0example0000005",
  });
  expect(within(docs).getByTestId("deploy-chip")).toHaveTextContent(
    "No deploys yet",
  );
}

test.fails(
  "[P2-14][FR-12.2] T-P2-14-05 card shows last deploy and previews",
  async () => {
    for (const viewport of VIEWPORTS) {
      const alone = await renderWithRouter(
        <DeployStatus apps={APPS} timeZone={TZ} />,
        { viewport },
      );
      expectStatus(screen.getByRole("list", { name: "Deployments" }));
      alone.unmount();

      const project = makeProject({ name: "Dune portal" });
      const { unmount } = await renderWithRouter(
        <ProjectCard project={project} timeZone={TZ} deploy={APPS} />,
        { viewport },
      );
      const card = screen.getByRole("article", { name: "Dune portal" });
      expectStatus(card);
      unmount();
    }

    // A project with no linked application shows no deploy list on its card.
    await renderWithRouter(
      <ProjectCard
        project={makeProject({ name: "Plain" })}
        timeZone={TZ}
        deploy={[]}
      />,
      { viewport: "laptop" },
    );
    const plain = screen.getByRole("article", { name: "Plain" });
    expect(
      within(plain).queryByRole("list", { name: "Deployments" }),
    ).toBeNull();
  },
);
