// The Guardrail dashboard (P4-01, FR-10.6): at Guardrail the dashboard shows one task, the
// first accepted plan item still to do (the In progress one first), with its first action
// and linked context, and only a count of the rest; Done reveals the next, which was
// prepared ahead (its task and packet already in the cache). At phone width (375 px) and
// on a laptop.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { focusCurrent, quietFocus } from "../../test/msw/focus";
import { guardrailTasks } from "../../test/msw/guardrail";
import { mondayPlan, planItem } from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { renderRoute, type Viewport } from "../../test/render";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const ACCEPTED = "2026-03-09T12:40:00Z";
const EMAIL = "Can you look at PR 42 before Friday?";

function guardrailPlan() {
  const items = [
    planItem(1, {
      title: "Write Acme invoice",
      label: "human",
      estimate_minutes: 30,
      first_action: "Open the invoice template",
      status: "in_progress",
      accepted_at: ACCEPTED,
    }),
    planItem(2, {
      title: "Review PR 42",
      label: "hybrid",
      estimate_minutes: 20,
      first_action: "Open the pull request",
      status: "today",
      accepted_at: ACCEPTED,
    }),
    planItem(3, {
      title: "Reply to Bob",
      label: "human",
      estimate_minutes: 10,
      first_action: "Open the thread",
      status: "today",
      accepted_at: ACCEPTED,
    }),
  ];
  return { items, plan: { current: mondayPlan({ items }) } };
}

test("[P4-01][FR-10.6] T-P4-01-09 shows one task and reveals next on Done", async () => {
  for (const viewport of VIEWPORTS) {
    const { items, plan } = guardrailPlan();
    const [first, second, third] = items;
    if (!first || !second || !third) throw new Error("three items");
    const reads = guardrailTasks(plan, { [second.task_id]: EMAIL });
    server.use(
      focusCurrent({
        ...quietFocus(),
        level: "guardrail",
        workspace_level: "guardrail",
      }),
      http.get("*/v1/plan/:day", () => HttpResponse.json(plan.current)),
      ...reads.handlers,
    );
    const view = await renderRoute("/", { viewport });

    const card = await screen.findByTestId("today-item");
    expect(screen.queryAllByTestId("today-item")).toHaveLength(1);
    expect(within(card).getByText("Write Acme invoice")).toBeInTheDocument();
    expect(
      within(card).getByText(/Open the invoice template/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Review PR 42")).toBeNull();
    expect(screen.queryByText("Reply to Bob")).toBeNull();
    expect(screen.getByText("2 more today")).toBeInTheDocument();
    // The next task is prepared before any click: its task and its packet.
    await waitFor(() => {
      expect(reads.counts.packet.get(second.task_id)).toBe(1);
    });
    expect(reads.counts.task.get(second.task_id)).toBe(1);

    await view.user.click(within(card).getByRole("button", { name: "Done" }));

    await waitFor(() => {
      expect(
        within(screen.getByTestId("today-item")).getByText("Review PR 42"),
      ).toBeInTheDocument();
    });
    expect(screen.queryAllByTestId("today-item")).toHaveLength(1);
    const next = screen.getByTestId("today-item");
    // Its linked email shows at once, from the prepared cache.
    expect(within(next).getByText(EMAIL)).toBeInTheDocument();
    expect(screen.getByText("1 more today")).toBeInTheDocument();
    expect(screen.queryByText("Write Acme invoice")).toBeNull();
    expect(reads.counts.packet.get(second.task_id)).toBe(1);
    view.unmount();
  }
});
