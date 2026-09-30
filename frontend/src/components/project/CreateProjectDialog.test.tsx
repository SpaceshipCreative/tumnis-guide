// Creating a project with its agent (P1-06, FR-2.1): the new-project form offers a new
// Hermes profile (the default) or linking an existing one by name, and the new project's
// header shows the agent's status as provisioning moves it on (live `agent_profile`).
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AgentProfileOut } from "../../api/types.gen";
import { connectLive } from "../../lib/ws";
import { FakeSocket } from "../../test/fakeSocket";
import { makeProject } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

const PROFILE_ID = "01950000-0000-7000-8000-000000000611";

function profileOf(projectId: string, status: string): AgentProfileOut {
  return {
    id: PROFILE_ID,
    name: "old-site",
    role: "project",
    transport: "daemon",
    project_id: projectId,
    runner_id: "01950000-0000-7000-8000-000000000401",
    endpoint: null,
    status,
    profile_version: null,
    health: null,
    health_checked_at: null,
    version: 1,
    created_at: "2026-03-09T12:00:00Z",
  };
}

beforeEach(() => {
  FakeSocket.reset();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test.fails("[P1-06][FR-2.1] create or link profile", async () => {
  const created = makeProject({ name: "Old site", client: null, goal: null });
  const fake = new ProjectFake({ project: created });
  const posts: unknown[] = [];
  let status = "provisioning";
  server.use(
    ...fake.handlers,
    http.get("/v1/projects", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
    http.post("/v1/projects", async ({ request }) => {
      posts.push(await request.json());
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get("/v1/agents/profiles", () =>
      HttpResponse.json({
        items: [profileOf(created.id, status)],
        next_cursor: null,
      }),
    ),
  );

  const { router, user, queryClient } = await renderRoute("/projects", {
    viewport: "phone",
  });
  const stop = connectLive(queryClient, "ws://localhost:3000/ws");
  FakeSocket.latest().open();

  await user.click(await screen.findByRole("button", { name: "New project" }));
  const form = screen.getByRole("form", { name: "New project" });
  const agent = within(form).getByRole("group", { name: "Agent" });
  // Create is the default, with nothing more to fill in.
  expect(
    within(agent).getByRole("radio", { name: "Create a new agent profile" }),
  ).toBeChecked();
  expect(within(form).queryByLabelText("Profile name")).toBeNull();

  await user.type(within(form).getByLabelText("Name"), "Old site");
  await user.click(
    within(agent).getByRole("radio", { name: "Link an existing profile" }),
  );
  // Link needs the profile's name before it saves.
  await user.click(within(form).getByRole("button", { name: "Save" }));
  expect(posts).toHaveLength(0);
  expect(within(form).getByLabelText("Profile name")).toBeInvalid();
  await user.type(within(form).getByLabelText("Profile name"), "old-site");
  await user.click(within(form).getByRole("button", { name: "Save" }));

  await waitFor(() => {
    expect(router.state.location.pathname).toBe(`/projects/${created.id}`);
  });
  expect(posts).toEqual([
    {
      name: "Old site",
      client: null,
      goal: null,
      profile: { mode: "link", name: "old-site" },
    },
  ]);
  expect(await screen.findByText("Agent: setting up")).toBeVisible();

  status = "ready";
  FakeSocket.latest().receive({ entity: "agent_profile", id: PROFILE_ID });
  expect(await screen.findByText("Agent: ready")).toBeVisible();

  status = "not_provisioned";
  FakeSocket.latest().receive({ entity: "agent_profile", id: PROFILE_ID });
  expect(await screen.findByText("Agent: not set up")).toBeVisible();
  stop();
});
