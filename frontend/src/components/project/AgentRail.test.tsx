// The Agent section of the project's Context rail (P2-17, FR-2.7): one line names the
// project's agent profile and its health; opening it in place shows the profile, its
// health, the workers and tools it declared at its last check, and the pause control.
// The tools are read only once the section opens. Every fixture is synthetic.
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { type ReactNode, useState } from "react";
import { expect, test } from "vitest";

import { factoryFor, makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { zAgentProfileOut, zProfileToolsOut } from "../../api/zod.gen";

interface AgentRailModule {
  AgentRail: (props: {
    projectId: string;
    open: boolean;
    onToggle: () => void;
    pause?: ReactNode;
  }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before AgentRail.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

const makeProfile = factoryFor(zAgentProfileOut);
const makeTools = factoryFor(zProfileToolsOut);

test("[P2-17][FR-2.7] T-P2-17-08 shows profile health workers tools and pause", async () => {
  const { AgentRail } = await load<AgentRailModule>("./AgentRail");
  const project = makeProject({ name: "Acme site" });
  const profile = makeProfile({
    name: "acme-site",
    role: "project",
    project_id: project.id,
    status: "ready",
    health: {
      reachable: true,
      authenticated: true,
      version: "1.4.0",
      status: "ok",
    },
    health_checked_at: "2026-03-09T11:00:00Z",
  });
  let toolReads = 0;
  server.use(
    http.get("/v1/agents/profiles", () =>
      HttpResponse.json({ items: [profile], next_cursor: null }),
    ),
    http.get(`/v1/agents/profiles/${profile.id}/tools`, () => {
      toolReads += 1;
      return HttpResponse.json(
        makeTools({
          profile_id: profile.id,
          profile_name: "acme-site",
          project_id: project.id,
          status: "ok",
          servers: [
            {
              name: "claude-code",
              transport: "stdio",
              target: "claude",
              allowed: true,
            },
            {
              name: "codex",
              transport: "stdio",
              target: "codex",
              allowed: true,
            },
            {
              name: "github",
              transport: "http",
              target: "api.github.com",
              allowed: true,
            },
          ],
        }),
      );
    }),
  );

  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <AgentRail
        projectId={project.id}
        open={open}
        onToggle={() => {
          setOpen((o) => !o);
        }}
        pause={<button type="button">Pause this project</button>}
      />
    );
  }
  const { user } = renderWithProviders(<Harness />);

  // One line: the profile and its health, closed.
  const summary = await screen.findByRole("button", { name: /Agent/ });
  expect(summary).toHaveAttribute("aria-expanded", "false");
  expect(await screen.findByText("acme-site · Healthy")).toBeVisible();
  expect(toolReads).toBe(0);

  // Opens in place.
  await user.click(summary);
  expect(summary).toHaveAttribute("aria-expanded", "true");
  const tools = await screen.findByRole("list", {
    name: "Workers and tools",
  });
  expect(tools).toHaveTextContent("claude-code");
  expect(tools).toHaveTextContent("codex");
  expect(tools).toHaveTextContent("github");
  expect(screen.getByText("Hermes 1.4.0")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Pause this project" }),
  ).toBeVisible();
  expect(toolReads).toBe(1);
});

test("[P2-17][FR-2.7] T-P2-17-08 says when the project has no agent yet", async () => {
  const { AgentRail } = await load<AgentRailModule>("./AgentRail");
  const project = makeProject({ name: "Beta app" });
  server.use(
    http.get("/v1/agents/profiles", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  );
  renderWithProviders(
    <AgentRail
      projectId={project.id}
      open={false}
      onToggle={() => undefined}
    />,
  );
  expect(await screen.findByText("No agent yet")).toBeVisible();
});
