// A task the plan could not place (P1-11, J6): the row says why in plain words and offers
// Split (into chunks that fit today's blocks) and Move (to the first day with room).
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  mondayPlan,
  ninetyMinuteIssue,
  planHandlers,
} from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithRouter, type Viewport } from "../../test/render";
import { FitOfferRow } from "./FitOfferRow";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const MONDAY = "2026-03-09";

test.fails("[P1-11][J6] split or move", async () => {
  const issue = ninetyMinuteIssue();
  for (const viewport of VIEWPORTS) {
    const recorder = new Recorder();
    server.use(...planHandlers(recorder, mondayPlan({ issues: [issue] })));
    const { user, unmount } = await renderWithRouter(
      <ul>
        <FitOfferRow issue={issue} day={MONDAY} />
      </ul>,
      { viewport },
    );
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("Write Acme proposal");
    expect(row).toHaveTextContent("No 90-minute gap today");

    // Split shows the chunks, then asks once more before it creates the subtasks.
    const split = within(row).getByRole("button", { name: /^Split/ });
    expect(split).toHaveTextContent("60 + 30");
    await user.click(split);
    await user.click(
      await within(row).findByRole("button", { name: "Accept split" }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/plan/${MONDAY}/issues/${issue.id}/split`,
      ]);
    });

    // Move goes to the first day with room, named in plain words.
    await user.click(
      within(row).getByRole("button", { name: "Move to Tue 10 Mar" }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/plan/${MONDAY}/issues/${issue.id}/split`,
        `POST /v1/plan/${MONDAY}/issues/${issue.id}/move`,
      ]);
    });
    unmount();
  }
});
