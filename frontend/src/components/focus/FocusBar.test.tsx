// The focus bar (P2-15, FR-10.3, FR-10.4, FR-10.9): on every signed-in page, the task in
// progress with its timer, the latest check-in with the rule that produced it, and
// one-tap replies that each post once; at phone width (375 px) and on a laptop.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  focusCurrent,
  focusMessage,
  focusReplies,
  focusSession,
  quietFocus,
} from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderRoute, type Viewport } from "../../test/render";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const ROUTES = ["/", "/review"] as const;

test("[P2-15][FR-10.3] T-P2-15-17 bar shows task timer replies and attribution", async () => {
  for (const viewport of VIEWPORTS) {
    for (const route of ROUTES) {
      const session = focusSession("Write proposal", 25);
      const checkIn = focusMessage({ task_id: session.task_id });
      const replies = focusReplies(quietFocus());
      server.use(
        focusCurrent({
          ...quietFocus(),
          level: "coach",
          workspace_level: "coach",
          session,
          messages: [checkIn],
        }),
        ...replies.handlers,
      );
      const view = await renderRoute(route, { viewport });

      const bar = await screen.findByRole("region", { name: "Focus" });
      expect(within(bar).getByText("Write proposal")).toBeInTheDocument();
      expect(within(bar).getByText("25 min")).toBeInTheDocument();
      expect(within(bar).getByText("Still on it?")).toBeInTheDocument();
      expect(
        within(bar).getByText("Coach · check_in_due (25 min cadence)"),
      ).toBeInTheDocument();
      for (const name of [
        "Still on it",
        "Switched",
        "Stuck",
        "Snooze",
        "Less of this",
      ]) {
        expect(within(bar).getByRole("button", { name })).toBeInTheDocument();
      }

      await view.user.click(
        within(bar).getByRole("button", { name: "Still on it" }),
      );
      await waitFor(() => {
        expect(replies.calls).toEqual([
          {
            path: "/focus/respond",
            body: { event_id: checkIn.id, response: "still_on_it" },
          },
        ]);
      });
      view.unmount();
    }
  }
});

test("[P2-15][FR-10.9] T-P2-15-17 less of this posts once and nothing shows at Quiet", async () => {
  const session = focusSession("Write proposal", 10);
  const checkIn = focusMessage({ task_id: session.task_id });
  const replies = focusReplies(quietFocus());
  server.use(
    focusCurrent({
      ...quietFocus(),
      level: "coach",
      workspace_level: "coach",
      session,
      messages: [checkIn],
    }),
    ...replies.handlers,
  );
  const view = await renderRoute("/", { viewport: "phone" });
  const bar = await screen.findByRole("region", { name: "Focus" });
  await view.user.click(
    within(bar).getByRole("button", { name: "Less of this" }),
  );
  await waitFor(() => {
    expect(replies.calls).toEqual([
      { path: "/focus/less", body: { event_id: checkIn.id } },
    ]);
  });
  view.unmount();

  server.use(focusCurrent(quietFocus()));
  await renderRoute("/", { viewport: "phone" });
  await screen.findByRole("main");
  expect(screen.queryByRole("region", { name: "Focus" })).toBeNull();
});

test("[P4-01][FR-10.9] T-P4-01-13 less of this lowers to Coach", async () => {
  for (const viewport of VIEWPORTS) {
    // Guardrail with a task in progress and no check-in waiting: "Less of this" is one
    // tap away all the same, and sets today's level to Coach.
    const session = focusSession("Write Acme invoice", 5);
    const lowered = {
      ...quietFocus(),
      level: "coach" as const,
      workspace_level: "guardrail" as const,
      override_level: "coach" as const,
      session,
    };
    const replies = focusReplies(lowered);
    server.use(
      focusCurrent({
        ...quietFocus(),
        level: "guardrail",
        workspace_level: "guardrail",
        session,
      }),
      ...replies.handlers,
    );
    const view = await renderRoute("/review", { viewport });
    const bar = await screen.findByRole("region", { name: "Focus" });
    expect(within(bar).getByTestId("focus-level")).toHaveTextContent(
      "Guardrail",
    );

    await view.user.click(
      within(bar).getByRole("button", { name: "Less of this" }),
    );

    await waitFor(() => {
      expect(replies.calls).toEqual([{ path: "/focus/less", body: {} }]);
    });
    await waitFor(() => {
      expect(within(bar).getByTestId("focus-level")).toHaveTextContent(
        "Coach today",
      );
    });
    view.unmount();
  }
});
