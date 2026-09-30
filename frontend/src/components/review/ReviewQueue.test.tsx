// The review queue (P1-13, UX 7, UX 11, FR-1.4, FR-6.1, R-04, R-05): every decision works
// from the keyboard with visible hints, the count follows live updates, and the queue
// stacks at phone width with its `kind` and `item` search params. The screen is reached
// through the real `/review` route; the API is MSW (src/test/msw/review.ts).
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { connectLive } from "../../lib/ws";
import { FakeSocket } from "../../test/fakeSocket";
import { reviewKinds } from "../../test/msw/handlers";
import { workingHoursHandlers } from "../../test/msw/planning";
import {
  makeReviewItem,
  reviewQueue,
  type ReviewItemFixture,
} from "../../test/msw/review";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

const PHASE_ONE = [
  "low_confidence_label",
  "decision_unavailable",
  "estimate_outlier",
  "provisioning_failed",
];

afterEach(() => {
  vi.useRealTimers();
  FakeSocket.reset();
});

function item(title: string, overrides: Partial<ReviewItemFixture> = {}) {
  return makeReviewItem({ target_title: title, ...overrides });
}

function card(title: string): HTMLElement {
  return screen.getByRole("article", { name: new RegExp(title) });
}

test(
  "[P1-13][UX 7] T-P1-13-12 keyboard primary edit reject snooze open",
  async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-03-09T14:00:00Z"));
    const [a, b, c, d, e] = [
      item("Task A"),
      item("Task B"),
      item("Task C", {
        kind: "provisioning_failed",
        target_type: "project",
        actions: ["accept", "reject", "snooze"],
        payload: { project_id: "p", error: "timeout", mode: "create" },
      }),
      item("Task D"),
      item("Task E", { project_id: "0b7f3c1e-8a55-4e8e-9a3c-2f4d6e8a1b2c" }),
    ];
    const queue = reviewQueue([a, b, c, d, e]);
    server.use(
      reviewKinds(PHASE_ONE),
      ...queue.handlers,
      ...workingHoursHandlers(new Recorder()),
    );
    const { user, router } = await renderRoute("/review");

    const hints = await screen.findByLabelText("Keyboard shortcuts");
    for (const key of ["Enter", "o", "e", "r", "s", "j", "k"]) {
      expect(within(hints).getByText(key)).toBeInTheDocument();
    }
    await waitFor(() => {
      expect(card("Task A")).toHaveFocus();
    });

    // Enter: the kind's primary action; focus moves to the next item
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(card("Task B")).toHaveFocus();
    });
    // e: edit, here picking the label (2 = AI)
    await user.keyboard("e");
    await user.keyboard("2");
    await waitFor(() => {
      expect(card("Task C")).toHaveFocus();
    });
    // r: reject
    await user.keyboard("r");
    await waitFor(() => {
      expect(card("Task D")).toHaveFocus();
    });
    // s, then 1: snooze for an hour
    await user.keyboard("s");
    await user.keyboard("1");
    await waitFor(() => {
      expect(card("Task E")).toHaveFocus();
    });

    const decided = queue.recorder.sent.filter((s) => s.method === "POST");
    expect(decided.map((s) => [s.path, s.body])).toEqual([
      [`/v1/review/${a.id}/decide`, { action: "accept", version: 1 }],
      [
        `/v1/review/${b.id}/decide`,
        { action: "edit", payload: { label: "ai" }, version: 1 },
      ],
      [`/v1/review/${c.id}/decide`, { action: "reject", version: 1 }],
      [
        `/v1/review/${d.id}/decide`,
        {
          action: "snooze",
          snooze_until: "2026-03-09T15:00:00.000Z",
          version: 1,
        },
      ],
    ]);
    expect(decided.every((s) => s.idempotencyKey)).toBe(true);

    // o: open the target (a task opens in its project's drawer)
    await user.keyboard("o");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(
        `/projects/${e.project_id ?? ""}`,
      );
    });
    expect(router.state.location.search).toMatchObject({
      task: e.target_id,
    });
  },
);

test("[P1-13][FR-1.4] T-P1-13-13 badge follows ws updates", async () => {
  vi.stubGlobal("WebSocket", FakeSocket);
  const queue = reviewQueue([item("Task A"), item("Task B")], 2);
  server.use(
    reviewKinds(PHASE_ONE),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );
  const { queryClient } = await renderRoute("/review");
  const stop = connectLive(queryClient, "ws://localhost/ws");
  try {
    FakeSocket.latest().open();
    expect(await screen.findByText("2 to review")).toBeInTheDocument();

    queue.setCount(3);
    FakeSocket.latest().receive({
      entity: "review_item",
      id: crypto.randomUUID(),
    });
    expect(await screen.findByText("3 to review")).toBeInTheDocument();

    queue.setCount(0);
    FakeSocket.latest().receive({
      entity: "review_item",
      id: crypto.randomUUID(),
    });
    expect(await screen.findByText("0 to review")).toBeInTheDocument();
  } finally {
    stop();
  }
});

test("[P1-13][UX 11] T-P1-13-14 phone layout", async () => {
  const later = item("Estimate check", {
    kind: "estimate_outlier",
    payload: {
      estimate_minutes: 15,
      flag: "too_low",
      score: 0.4,
      history_median: 90,
    },
  });
  const future = item("Pick a colour", {
    kind: "future_question",
    actions: ["answer", "snooze"],
    payload: { prompt: "Which colour?" },
  });
  const queue = reviewQueue([item("Task A"), later, future]);
  server.use(
    reviewKinds([...PHASE_ONE, "future_question"]),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );

  // a registered kind and an item open that item, filtered to the kind
  const { unmount } = await renderRoute(
    `/review?kind=estimate_outlier&item=${later.id}`,
    { viewport: "phone" },
  );
  await waitFor(() => {
    expect(card("Estimate check")).toHaveFocus();
  });
  expect(card("Estimate check")).toHaveAttribute("aria-current", "true");
  expect(queue.reads.at(-1)?.get("kind")).toBe("estimate_outlier");
  expect(screen.queryByRole("article", { name: /Task A/ })).toBeNull();
  const buttons = within(card("Estimate check")).getAllByRole("button");
  expect(buttons.map((b) => b.textContent)).toEqual(
    expect.arrayContaining(["Accept", "Edit", "Reject", "Snooze"]),
  );
  for (const button of buttons) {
    expect(button.className).toContain("min-h-11"); // 44 px touch targets
  }
  expect(
    screen.getByRole("list", { name: "Review queue" }).className,
  ).toContain("flex-col");
  unmount();

  // any registered string is a kind; its items render with the generic renderer
  const second = await renderRoute("/review?kind=future_question", {
    viewport: "phone",
  });
  expect(
    await screen.findByRole("article", { name: /Pick a colour/ }),
  ).toBeInTheDocument();
  expect(
    within(card("Pick a colour")).getByRole("button", { name: "Answer" }),
  ).toBeInTheDocument();
  second.unmount();

  // an unknown kind falls back to all kinds
  const fallback = await renderRoute("/review?kind=nope", {
    viewport: "phone",
  });
  await waitFor(() => {
    expect(fallback.router.state.location.search).toEqual({});
  });
  expect(
    await screen.findByRole("article", { name: /Task A/ }),
  ).toBeInTheDocument();
  expect(queue.reads.at(-1)?.get("kind")).toBeNull();
});
