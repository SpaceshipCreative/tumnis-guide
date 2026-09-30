// The task drawer (P0-24, FR-3.5): `?task=<id>` opens it; the recurrence picker saves
// the task's repeat rule with one PUT.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P0-24][FR-3.5] T-P0-24-16 drawer opens from the task param and saves a recurrence", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({
      project_id: project.id,
      title: "Send the weekly report",
      status: "backlog",
      version: 7,
    });
    const fake = new ProjectFake({ project, tasks: [task] });
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?task=${task.id}`,
      { viewport },
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Send the weekly report",
    });
    expect(within(drawer).getByRole("textbox", { name: "Title" })).toHaveValue(
      "Send the weekly report",
    );
    const repeats = within(drawer).getByRole("combobox", { name: "Repeats" });
    expect(repeats).toHaveValue("never");
    await user.selectOptions(repeats, "weekly");
    await user.selectOptions(
      within(drawer).getByRole("combobox", { name: "Day" }),
      "0",
    );
    expect(within(drawer).getByLabelText("Time")).toHaveValue("09:00");
    await user.click(
      within(drawer).getByRole("button", { name: "Save repeat" }),
    );

    await waitFor(() => {
      expect(
        fake.sent("PUT", `/v1/tasks/${task.id}/recurrence`).map((s) => s.body),
      ).toEqual([
        { preset: "weekly", weekday: 0, due_time: "09:00", version: 7 },
      ]);
    });
    expect(
      await within(drawer).findByText("Repeats weekly on Monday at 09:00"),
    ).toBeVisible();
    unmount();
  }
});

test("[P1-08][UX 9] T-P1-08-16 enrichment undo", async () => {
  // The drawer shows what the agent filled (criteria, estimate, first action) with
  // `Enriched by agent · Undo`; Undo sends the enrichment's change with the task's
  // version and the drawer shows the values from before it.
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const changeId = crypto.randomUUID();
    const enriched = {
      ...makeTask({
        project_id: project.id,
        title: "Send Acme the March invoice",
        label: "human",
        status: "backlog",
      }),
      first_action: "Open last month's invoice in Wave and duplicate it",
      first_action_source: "agent",
      enrichment_status: "done",
      acceptance_criteria:
        "- The March invoice is sent to the client\n- The total matches the rate card",
      estimate_minutes: 20,
      change_id: changeId,
      version: 4,
    };
    const before = {
      ...enriched,
      first_action: "Open the invoice template",
      first_action_source: "placeholder",
      acceptance_criteria: null,
      estimate_minutes: null,
      change_id: crypto.randomUUID(),
      version: 5,
    };
    let current: Record<string, unknown> = enriched;
    const undoBodies: unknown[] = [];
    const fake = new ProjectFake({ project, tasks: [enriched] });
    server.resetHandlers();
    server.use(...fake.handlers);
    server.use(
      http.get(`/v1/tasks/${enriched.id}`, () => HttpResponse.json(current)),
      http.post(`/v1/tasks/${enriched.id}/undo`, async ({ request }) => {
        undoBodies.push(await request.json());
        current = before;
        return HttpResponse.json(before);
      }),
    );

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?task=${enriched.id}`,
      { viewport },
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Send Acme the March invoice",
    });
    expect(await within(drawer).findByText("Enriched by agent")).toBeVisible();
    const criteria = within(drawer).getByRole("list", {
      name: "Acceptance criteria",
    });
    expect(within(criteria).getAllByRole("listitem")).toHaveLength(2);
    expect(within(drawer).getByText("20 min")).toBeVisible();
    expect(
      within(drawer).getByText(
        "Open last month's invoice in Wave and duplicate it",
      ),
    ).toBeVisible();

    await user.click(within(drawer).getByRole("button", { name: "Undo" }));

    await waitFor(() => {
      expect(undoBodies).toEqual([{ change_id: changeId, version: 4 }]);
    });
    expect(
      await within(drawer).findByText("Open the invoice template"),
    ).toBeVisible();
    expect(within(drawer).getByText("No estimate")).toBeVisible();
    expect(
      within(drawer).queryByRole("list", { name: "Acceptance criteria" }),
    ).toBeNull();
    expect(within(drawer).queryByText("Enriched by agent")).toBeNull();
    unmount();
  }
});
