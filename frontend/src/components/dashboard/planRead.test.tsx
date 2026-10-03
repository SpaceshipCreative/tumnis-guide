// APP-F05 (final application test, was APP-10; Scott decision 98): before the planner
// publishes the day's plan, `GET /v1/plan/{day}` answers 200 with `null` instead of a 404
// the browser logs as a console error. The generated client takes `null` as an answer, and
// the dashboard reads it as "no plan yet": the Today panel shows its Today tasks and the
// Guardrail dashboard offers a re-plan, neither says the plan could not be loaded.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { planningGetPlan } from "../../api/sdk.gen";
import { makeTask } from "../../test/factories";
import { focusCurrent, quietFocus } from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderRoute, renderWithRouter } from "../../test/render";
import { TodayPanel } from "./TodayPanel";

const MONDAY = "2026-03-09";
const ACME = crypto.randomUUID();

function planNotYetPublished() {
  return http.get("*/v1/plan/:day", () => HttpResponse.json(null));
}

test.fails(
  "[P1-11][FR-4.3] APP-F05 the client reads a 200 null plan as no plan, not an error",
  async () => {
    server.use(planNotYetPublished());
    const answer = await planningGetPlan({
      path: { day: MONDAY },
      throwOnError: true,
    });
    expect(answer.response.status).toBe(200);
    expect(answer.data).toBeNull();
  },
);

test("[P1-11][FR-1.2] APP-F05 with no plan yet the Today panel shows its Today tasks", async () => {
  server.use(planNotYetPublished());
  await renderWithRouter(
    <TodayPanel
      items={[
        makeTask({
          project_id: ACME,
          title: "Tidy the drive",
          status: "today",
        }),
      ]}
      total={1}
      projectNames={{ [ACME]: "Acme site" }}
      planDay={MONDAY}
    />,
  );
  const today = screen.getByRole("region", { name: "Today" });
  expect(await within(today).findByText("Tidy the drive")).toBeInTheDocument();
  expect(today).not.toHaveTextContent("could not be loaded");
});

test.fails(
  "[P4-01][FR-10.6] APP-F05 with no plan yet Guardrail offers a re-plan, not unavailable",
  async () => {
    server.use(
      focusCurrent({
        ...quietFocus(),
        level: "guardrail",
        workspace_level: "guardrail",
      }),
      planNotYetPublished(),
    );
    await renderRoute("/", { viewport: "laptop" });

    const now = await screen.findByRole("region", { name: "Now" });
    expect(
      await within(now).findByText("Nothing planned. Re-plan?"),
    ).toBeInTheDocument();
    expect(
      within(now).queryByText("Today's plan could not be loaded."),
    ).toBeNull();
  },
);
