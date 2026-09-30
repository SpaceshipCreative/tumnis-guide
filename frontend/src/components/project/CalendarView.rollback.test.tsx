// A refused schedule rolls back the week it changed, even when the view has moved to
// another week while the PATCH was in flight (PR #77 review): the next week's cache
// entry must not receive the previous week's snapshot.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import type { WeekOut } from "../../api/types.gen";
import { layOutWeek } from "../../test/calendarLayout";
import { server } from "../../test/msw/server";
import { MONDAY, PROJECT_ID, weekPayload } from "../../test/msw/week";
import { renderWithProviders } from "../../test/render";
import { CalendarView, weekQuery } from "./CalendarView";

const NOW = new Date("2026-03-09T12:00:00Z");
const NEXT_MONDAY = "2026-03-16";

afterEach(() => {
  vi.useRealTimers();
});

function Harness() {
  const [week, setWeek] = useState(MONDAY);
  return <CalendarView projectId={PROJECT_ID} week={week} onWeek={setWeek} />;
}

test("[P1-12][FR-2.6] a refused drop rolls back its own week after a week change", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
  layOutWeek();
  let release: (() => void) | undefined;
  const answered = new Promise<void>((resolve) => {
    release = resolve;
  });
  let gets = 0;
  server.use(
    http.get("*/v1/plan/week/:monday", async ({ params }) => {
      gets += 1;
      // The two first loads answer; the refetches after the refusal never do, so the
      // cache shows what the rollback wrote.
      if (gets > 2) await new Promise(() => undefined);
      return HttpResponse.json({
        ...weekPayload(),
        monday: String(params.monday),
      } satisfies WeekOut);
    }),
    http.patch("*/v1/plan/:day/items/:taskId", async () => {
      await answered;
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          code: "block_not_free",
          detail: "That time is not free for this task.",
        },
        {
          status: 409,
          headers: { "Content-Type": "application/problem+json" },
        },
      );
    }),
  );
  const { user, queryClient } = renderWithProviders(<Harness />, {
    viewport: "laptop",
  });
  const card = await screen.findByRole("button", {
    name: "Draft the brand guide, 45 minutes",
  });
  card.focus();
  await user.keyboard(" "); // picks it up at the first free slot
  await user.keyboard(" "); // drops: the PATCH waits

  await user.click(screen.getByRole("button", { name: "Next week" }));
  await waitFor(() => {
    expect(
      queryClient.getQueryData(weekQuery(PROJECT_ID, NEXT_MONDAY).queryKey)
        ?.monday,
    ).toBe(NEXT_MONDAY);
  });

  release?.();
  expect(
    await screen.findByText("That time is no longer free"),
  ).toBeInTheDocument();
  expect(
    queryClient.getQueryData(weekQuery(PROJECT_ID, NEXT_MONDAY).queryKey)
      ?.monday,
  ).toBe(NEXT_MONDAY);
  const first = queryClient.getQueryData(
    weekQuery(PROJECT_ID, MONDAY).queryKey,
  );
  expect(first?.monday).toBe(MONDAY);
  expect(first?.unscheduled.map((t) => t.title)).toContain(
    "Draft the brand guide",
  );
});
