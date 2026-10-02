// The J1 A1.2 flake: the day's plan read answered 429 (the per-principal rate limit) and
// the Today panel showed the Today tasks instead of the plan. A 429 is tried again after
// its Retry-After, so the plan shows; other 4xx answers stay final.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import { mondayPlan } from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { renderWithRouter } from "../../test/render";
import { TodayPanel } from "./TodayPanel";

const MONDAY = "2026-03-09";
const ACME = crypto.randomUUID();

test("[P1-11][J1] a plan read refused with 429 is read again and the plan shows", async () => {
  let reads = 0;
  server.use(
    http.get("*/v1/plan/:day", () => {
      reads += 1;
      if (reads === 1) {
        return HttpResponse.json(
          { title: "Rate limited", status: 429, code: "rate_limited" },
          { status: 429, headers: { "Retry-After": "0" } },
        );
      }
      return HttpResponse.json(mondayPlan());
    }),
  );
  const today = [
    makeTask({ project_id: ACME, title: "Tidy the drive", status: "today" }),
  ];
  await renderWithRouter(
    <TodayPanel
      items={today}
      total={1}
      projectNames={{ [ACME]: "Acme site" }}
      planDay={MONDAY}
    />,
  );
  const panel = screen.getByRole("region", { name: "Today" });
  expect(await within(panel).findByText("Send logo drafts")).toBeVisible();
  expect(within(panel).queryByText("Tidy the drive")).not.toBeInTheDocument();
  expect(reads).toBe(2);
});
