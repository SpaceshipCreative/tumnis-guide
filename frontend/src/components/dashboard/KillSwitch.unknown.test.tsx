// The kill switch stays usable when its state cannot be read (P2-09, SAF-4): a failed
// `GET /v1/agents/pause` still shows "Pause all agents", so a broken read never hides the
// safety control.
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { KillSwitch } from "./KillSwitch";

test("[P2-09][SAF-4] a failed pause-state read still shows the pause control", async () => {
  server.use(
    http.get("*/v1/agents/pause", () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "Unavailable",
          status: 503,
          code: "unavailable",
        },
        { status: 503 },
      ),
    ),
  );
  renderWithProviders(<KillSwitch />);
  expect(
    await screen.findByRole("button", { name: "Pause all agents" }),
  ).toBeEnabled();
  expect(screen.queryByRole("status", { name: "Agents paused" })).toBeNull();
});
