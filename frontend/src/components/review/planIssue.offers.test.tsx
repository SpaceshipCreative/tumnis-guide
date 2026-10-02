// The plan_issue review item's actions (P1-13 with P1-11, J6; CodeRabbit on #158). Accept
// takes the split and Edit takes the move (plan: "accept takes the split, edit takes the
// move"), and the kind defines no edit payload. Two defects: Accept and Edit showed when
// the item had no split or no move on offer, so the planner refused them (409 no_split,
// no_move), and Edit opened a text form whose `{value}` the server refuses (422
// invalid_review_payload; APP-12). An action with no offer is not shown (the reason is
// read to screen readers), and Edit decides the move at once with no payload, from the
// button and from the keyboard. Every fixture is synthetic.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { reviewKinds } from "../../test/msw/handlers";
import { workingHoursHandlers } from "../../test/msw/planning";
import { makeReviewItem, reviewQueue } from "../../test/msw/review";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";
import { ReviewItemCard, type DecideAction, type Mode } from "./ReviewItemCard";

const planIssue = (title: string, payload: Record<string, unknown>) =>
  makeReviewItem({
    kind: "plan_issue",
    target_title: title,
    payload: {
      plan_id: crypto.randomUUID(),
      day: "2026-10-01",
      title,
      estimate_minutes: 90,
      reason: "No 90-minute gap today",
      split: null,
      move_to: null,
      ...payload,
    },
  });

function renderCard(item: ReturnType<typeof planIssue>) {
  const onDecide = vi.fn<(decision: DecideAction) => void>();
  const onMode = vi.fn<(mode: Mode) => void>();
  render(
    <ReviewItemCard
      item={item}
      current={false}
      mode="idle"
      busy={false}
      articleRef={() => undefined}
      onFocus={() => undefined}
      onMode={onMode}
      onDecide={onDecide}
      onOpen={() => undefined}
    />,
  );
  return { onDecide, onMode };
}

test("[P1-13][UX 7] a plan issue offers only the actions it has an offer for", () => {
  renderCard(planIssue("Write Acme proposal", { move_to: "2026-10-02" }));
  expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
  expect(screen.getByText(/Accept is not offered/)).toBeInTheDocument();
});

test("[P1-13][UX 7] a plan issue with neither offer keeps only reject and snooze", () => {
  renderCard(planIssue("Write Acme proposal", {}));
  expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Snooze" })).toBeVisible();
  expect(screen.getByText(/Edit is not offered/)).toBeInTheDocument();
});

test("[P1-13][UX 7] APP-12 Edit on a plan issue takes the move with no payload", async () => {
  const { onDecide, onMode } = renderCard(
    planIssue("Write Acme proposal", {
      split: [60, 30],
      move_to: "2026-10-02",
    }),
  );
  expect(screen.getByRole("button", { name: "Accept" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(onDecide).toHaveBeenCalledWith({ action: "edit" });
  expect(onMode).not.toHaveBeenCalledWith("edit");
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("[P1-13][UX 11] APP-12 the keyboard decides a plan issue's offers the same way", async () => {
  const noSplit = planIssue("Write Acme proposal", { move_to: "2026-10-02" });
  const queue = reviewQueue([noSplit]);
  server.use(
    reviewKinds(["plan_issue"]),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );
  const { user } = await renderRoute("/review");
  const card = await screen.findByRole("article", {
    name: /Write Acme proposal/,
  });
  await waitFor(() => {
    expect(card).toHaveFocus();
  });

  // Enter: the primary action is Accept, which has no split to take: nothing is sent.
  await user.keyboard("{Enter}");
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(queue.recorder.sent.filter((s) => s.method === "POST")).toEqual([]);
  expect(screen.queryByRole("textbox")).toBeNull();

  // e: the move, decided at once with no payload.
  await user.keyboard("e");
  await waitFor(() => {
    expect(
      queue.recorder.sent
        .filter((s) => s.method === "POST")
        .map((s) => [s.path, s.body]),
    ).toEqual([
      [`/v1/review/${noSplit.id}/decide`, { action: "edit", version: 1 }],
    ]);
  });
  expect(screen.queryByRole("textbox")).toBeNull();
});
