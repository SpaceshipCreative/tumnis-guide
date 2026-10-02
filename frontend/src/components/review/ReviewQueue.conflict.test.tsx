// Review queue conflicts (A2.1, journey J3): the worker updates an open item's ranking
// fields (blocking impact, Jev's factor) after it was created, and each update bumps its
// version. A decision sent at the version the queue read then answers 409 stale_version
// although nothing the person decided on changed: the queue sends it once more at the
// current version. A real change (the item decided, snoozed or its content edited) still
// stops and says so. Every fixture is synthetic.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

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

const KINDS = ["low_confidence_label"];

function card(title: string): HTMLElement {
  return screen.getByRole("article", { name: new RegExp(title) });
}

/** The first decide answers 409 stale_version with `current`; later ones fall through. */
function staleOnce(current: Record<string, unknown>) {
  let answered = false;
  return http.post("*/v1/review/:id/decide", () => {
    if (answered) return undefined;
    answered = true;
    return HttpResponse.json(
      {
        type: "about:blank",
        title: "Conflict",
        status: 409,
        code: "stale_version",
        current,
      },
      { status: 409 },
    );
  });
}

async function decideFirst(
  a: ReviewItemFixture,
  current: Record<string, unknown>,
) {
  const queue = reviewQueue([a, makeReviewItem({ target_title: "Task B" })]);
  server.use(
    reviewKinds(KINDS),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );
  server.use(staleOnce(current));
  const { user } = await renderRoute("/review");
  await waitFor(() => {
    expect(card("Task A")).toHaveFocus();
  });
  await user.keyboard("{Enter}");
  return queue;
}

test("[A2.1] a decision that only met a ranking update goes through at the current version", async () => {
  const a = makeReviewItem({ target_title: "Task A" });
  const queue = await decideFirst(a, {
    ...a,
    version: 3,
    jev_factor: 1.4,
    blocking_impact: "2.5",
  });

  await waitFor(() => {
    expect(card("Task B")).toHaveFocus();
  });
  const sent = queue.recorder.sent.filter((s) => s.method === "POST");
  expect(sent.map((s) => s.body)).toEqual([{ action: "accept", version: 3 }]);
  expect(screen.queryByText(/changed elsewhere/i)).toBeNull();
});

test("[A2.1] a decision on an item someone else decided still stops", async () => {
  const a = makeReviewItem({ target_title: "Task A" });
  const queue = await decideFirst(a, {
    ...a,
    version: 2,
    decided_at: "2026-03-09T12:30:00Z",
    decision: "reject",
  });

  expect(await screen.findByText(/changed elsewhere/i)).toBeInTheDocument();
  expect(queue.recorder.sent.filter((s) => s.method === "POST")).toEqual([]);
});

test("[A2.1] a decision on an item whose content changed still stops", async () => {
  const a = makeReviewItem({ target_title: "Task A" });
  const queue = await decideFirst(a, {
    ...a,
    version: 2,
    payload: { ...a.payload, suggested: "ai" },
  });

  expect(await screen.findByText(/changed elsewhere/i)).toBeInTheDocument();
  expect(queue.recorder.sent.filter((s) => s.method === "POST")).toEqual([]);
});
