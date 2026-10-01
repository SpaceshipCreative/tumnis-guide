// The dashboard's focus level chip (P2-15, FR-10.1, FR-10.9): the level in force, the
// workspace level in one select (`PUT /v1/focus/level`) and "Less today"
// (`POST /v1/focus/less`); each answer is the new focus state, shown at once.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { focusCurrent, focusReplies, quietFocus } from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { FocusLevel } from "./FocusLevel";

const COACH = {
  ...quietFocus(),
  level: "coach",
  workspace_level: "coach",
} as const;

test("[P2-15][FR-10.1] the chip changes the workspace level with one PUT", async () => {
  const replies = focusReplies({
    ...quietFocus(),
    level: "nudge",
    workspace_level: "nudge",
  });
  server.use(focusCurrent(COACH), ...replies.handlers);
  const { user } = renderWithProviders(<FocusLevel />, { viewport: "phone" });

  expect(await screen.findByText("Focus: Coach")).toBeInTheDocument();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Workspace focus level" }),
    "nudge",
  );

  await waitFor(() => {
    expect(replies.calls).toEqual([
      { path: "/focus/level", body: { level: "nudge" } },
    ]);
  });
  expect(await screen.findByText("Focus: Nudge")).toBeInTheDocument();
});

test("[P2-15][FR-10.9] less today lowers today's level and says so", async () => {
  const replies = focusReplies({
    ...COACH,
    level: "nudge",
    override_level: "nudge",
  });
  server.use(focusCurrent(COACH), ...replies.handlers);
  const { user } = renderWithProviders(<FocusLevel />, { viewport: "phone" });

  await user.click(await screen.findByRole("button", { name: "Less today" }));

  await waitFor(() => {
    expect(replies.calls).toEqual([{ path: "/focus/less", body: {} }]);
  });
  expect(
    await screen.findByText("Focus today: Nudge (usually Coach)"),
  ).toBeInTheDocument();
});

test("[P2-15][FR-10.9] at Quiet there is nothing to lower", async () => {
  server.use(focusCurrent(quietFocus()));
  renderWithProviders(<FocusLevel />, { viewport: "phone" });

  expect(await screen.findByText("Focus: Quiet")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Less today" })).toBeNull();
});
