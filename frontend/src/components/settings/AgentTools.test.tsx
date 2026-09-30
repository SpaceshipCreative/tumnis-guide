// Settings > Agents > a profile's tools (P2-10, FR-5.12): the MCP servers the profile
// really has, read-only, each matched against the project's allowlist, and each token's
// reach, at phone and laptop widths.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders, type Viewport } from "../../test/render";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const PROFILE_ID = "01950000-0000-7000-8000-000000000801";

interface AgentToolsModule {
  AgentTools: (props: { profileId: string }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before AgentTools.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

const TOOLS = {
  profile_id: PROFILE_ID,
  profile_name: "acme-site",
  project_id: "01950000-0000-7000-8000-000000000802",
  checked_at: "2026-03-09T12:00:00Z",
  status: "degraded",
  reachable: true,
  authenticated: true,
  hermes_version: "0.9.1",
  profile_version: "1.0.0",
  allowlist: ["tumnis", "jev", "github", "coolify"],
  servers: [
    {
      name: "tumnis",
      transport: "http",
      target: "tumnis.example.org",
      allowed: true,
    },
    { name: "jev", transport: "stdio", target: "uvx", allowed: true },
    { name: "github", transport: "stdio", target: "npx", allowed: true },
    { name: "shell", transport: "stdio", target: "bash", allowed: false },
  ],
  extra: ["shell"],
  missing: ["coolify"],
  github: {
    token_present: true,
    own_reachable: { "acme/site": true },
    foreign_reachable: ["beta/app"],
    errors: [],
  },
  coolify: null,
};

test.fails(
  "[P2-10][FR-5.12] lists servers read-only with match state",
  async () => {
    const { AgentTools } = await load<AgentToolsModule>("./AgentTools");
    for (const viewport of VIEWPORTS) {
      server.use(
        http.get("*/v1/agents/profiles/:id/tools", ({ params }) =>
          params.id === PROFILE_ID
            ? HttpResponse.json(TOOLS)
            : HttpResponse.json({ code: "not_found" }, { status: 404 }),
        ),
      );
      const { unmount } = renderWithProviders(
        <AgentTools profileId={PROFILE_ID} />,
        { viewport },
      );

      const servers = await screen.findByRole("list", { name: "MCP servers" });
      const allowed = within(servers).getByRole("listitem", { name: "tumnis" });
      expect(within(allowed).getByText("Allowed")).toBeInTheDocument();
      expect(
        within(allowed).getByText(/tumnis\.example\.org/),
      ).toBeInTheDocument();
      const extra = within(servers).getByRole("listitem", { name: "shell" });
      expect(within(extra).getByText("Not allowed")).toBeInTheDocument();
      expect(extra).toHaveAttribute("data-drift", "extra");
      expect(allowed).not.toHaveAttribute("data-drift");

      const missing = screen.getByRole("list", { name: "Missing servers" });
      expect(within(missing).getByText("coolify")).toBeInTheDocument();
      expect(screen.getByText(/beta\/app/)).toBeInTheDocument();
      expect(screen.getByText(/Hermes 0\.9\.1/)).toBeInTheDocument();
      expect(screen.getByText(/profile 1\.0\.0/)).toBeInTheDocument();

      // Read-only: nothing here edits the profile (FR-5.12).
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      expect(screen.queryAllByRole("textbox")).toHaveLength(0);
      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
      expect(screen.queryAllByRole("combobox")).toHaveLength(0);
      unmount();
    }
  },
);
