// Settings > Agents (P1-04, FR-5.9): runners online and offline, profile health chips, and
// a new runner's device token shown once, at phone and laptop widths.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import {
  agentsHandlers,
  NEW_RUNNER_TOKEN,
  PROFILES,
  Recorder,
  RUNNERS,
} from "../../test/msw/settings";
import { renderWithProviders, type Viewport } from "../../test/render";
import { AgentsSection } from "./AgentsSection";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];

test.fails("[P1-04][FR-5.9] T-P1-04-19 shows runner and profile health", async () => {
  for (const viewport of VIEWPORTS) {
    const recorder = new Recorder();
    server.use(...agentsHandlers(recorder));
    const { user, unmount } = renderWithProviders(<AgentsSection />, {
      viewport,
    });

    const [online, offline] = RUNNERS;
    const [master, beta] = PROFILES;
    if (!online || !offline || !master || !beta) throw new Error("fixtures");

    const runners = await screen.findByRole("list", { name: "Runners" });
    const onlineItem = within(runners).getByRole("listitem", {
      name: online.name,
    });
    expect(within(onlineItem).getByText("Online")).toBeInTheDocument();
    expect(
      within(onlineItem).getByText(/hermes 0\.9\.1/i),
    ).toBeInTheDocument();
    const offlineItem = within(runners).getByRole("listitem", {
      name: offline.name,
    });
    expect(within(offlineItem).getByText("Offline")).toBeInTheDocument();

    const profiles = screen.getByRole("list", { name: "Agent profiles" });
    const masterItem = within(profiles).getByRole("listitem", {
      name: master.name,
    });
    expect(within(masterItem).getByText("Healthy")).toBeInTheDocument();
    expect(within(masterItem).getByText("Authenticated")).toBeInTheDocument();
    expect(within(masterItem).getByText(/0\.9\.1/)).toBeInTheDocument();
    const betaItem = within(profiles).getByRole("listitem", {
      name: beta.name,
    });
    expect(within(betaItem).getByText("Unreachable")).toBeInTheDocument();

    // Check health asks the api for a new check.
    await user.click(
      within(masterItem).getByRole("button", { name: "Check health" }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/agents/profiles/${master.id}/health-check`,
      ]);
    });

    // A new runner's token is shown once, then gone.
    await user.type(screen.getByLabelText("Runner name"), "garage-hermes");
    await user.click(screen.getByRole("button", { name: "Add runner" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Copy the runner's token",
    });
    expect(within(dialog).getByText(NEW_RUNNER_TOKEN)).toBeInTheDocument();
    expect(recorder.sent.at(-1)?.body).toEqual({ name: "garage-hermes" });
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(screen.queryByText(NEW_RUNNER_TOKEN)).not.toBeInTheDocument();
    for (const sent of recorder.sent) {
      expect(sent.idempotencyKey).toBeTruthy();
    }
    unmount();
  }
});
