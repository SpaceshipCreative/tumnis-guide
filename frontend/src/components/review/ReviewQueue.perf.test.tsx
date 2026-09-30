// The review queue's ready mark (P0-29, PERF-2): `tumnis:review-ready` fires once, when the
// queue has rendered with its data, so the cold-open timing measures a usable screen.
// Every fixture is synthetic.
import { screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { reviewKinds } from "../../test/msw/handlers";
import { workingHoursHandlers } from "../../test/msw/planning";
import { makeReviewItem, reviewQueue } from "../../test/msw/review";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";
import { REVIEW_READY_MARK } from "./ReviewQueue";

test("[P0-29][PERF-2] marks tumnis:review-ready once the queue has data", async () => {
  const mark = vi.spyOn(performance, "mark");
  const queue = reviewQueue([makeReviewItem({ target_title: "Task A" })]);
  server.use(
    reviewKinds(["low_confidence_label"]),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );

  await renderRoute("/review");

  await screen.findByRole("article", { name: /Task A/ });
  await waitFor(() => {
    expect(
      mark.mock.calls.filter(([name]) => name === REVIEW_READY_MARK),
    ).toHaveLength(1);
  });
  expect(REVIEW_READY_MARK).toBe("tumnis:review-ready");
});
