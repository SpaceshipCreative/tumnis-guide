// The Today panel (P0-23, FR-1.2, UX 3, UX 5): at most five tasks in the server's order,
// each with its project, label, estimate (Human and Hybrid only) and first action, then
// "+N more" to the full Today list. With the day's published plan (P1-11): the plan's
// items with their reasons and blocks, accept, swap and remove, the fallback notice.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import { ALTERNATES, mondayPlan, planHandlers } from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithRouter, type Viewport } from "../../test/render";
import { TodayPanel } from "./TodayPanel";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const ACME = crypto.randomUUID();
const DOGFOOD = crypto.randomUUID();
const PROJECT_NAMES = { [ACME]: "Acme site", [DOGFOOD]: "Tumnis dogfood" };

function rowsOf(panel: HTMLElement): HTMLElement[] {
  return within(panel).getAllByRole("listitem");
}

test("[P0-23][FR-1.2][UX 3] T-P0-23-03 shows at most 5, in server order, with project, label, estimate and first action", async () => {
  // Server order (today order), deliberately not alphabetical.
  const items = [
    makeTask({
      project_id: ACME,
      title: "Send logo drafts",
      label: "human",
      estimate_minutes: 45,
      first_action: "Export the PDFs",
      status: "today",
    }),
    makeTask({
      project_id: DOGFOOD,
      title: "Book the venue",
      label: "hybrid",
      estimate_minutes: 90,
      first_action: "Call the hall",
      status: "today",
    }),
    makeTask({
      project_id: ACME,
      title: "Write the brief",
      label: "human",
      estimate_minutes: 30,
      first_action: "Open the template",
      status: "today",
    }),
    makeTask({
      project_id: DOGFOOD,
      title: "Answer Acme",
      label: "hybrid",
      estimate_minutes: 15,
      first_action: "Reread the thread",
      status: "today",
    }),
    makeTask({
      project_id: ACME,
      title: "Call the printer",
      label: "human",
      estimate_minutes: 60,
      first_action: "Find the quote",
      status: "today",
    }),
  ];
  const expected = [
    ["Send logo drafts", "Acme site", "Human", "45 min", "Export the PDFs"],
    [
      "Book the venue",
      "Tumnis dogfood",
      "Hybrid",
      "1 h 30 min",
      "Call the hall",
    ],
    ["Write the brief", "Acme site", "Human", "30 min", "Open the template"],
    ["Answer Acme", "Tumnis dogfood", "Hybrid", "15 min", "Reread the thread"],
    ["Call the printer", "Acme site", "Human", "1 h", "Find the quote"],
  ];

  for (const viewport of VIEWPORTS) {
    const { unmount } = await renderWithRouter(
      <TodayPanel items={items} total={7} projectNames={PROJECT_NAMES} />,
      { viewport },
    );
    const panel = screen.getByRole("region", { name: "Today" });
    const rows = rowsOf(panel);
    expect(rows).toHaveLength(5);
    rows.forEach((row, i) => {
      const [title, project, label, estimate, firstAction] = expected[i] ?? [];
      expect(
        within(row).getByText(title ?? "", { exact: true }),
      ).toBeInTheDocument();
      expect(
        within(row).getByText(project ?? "", { exact: true }),
      ).toBeInTheDocument();
      expect(
        within(row).getByText(label ?? "", { exact: true }),
      ).toBeInTheDocument();
      expect(
        within(row).getByText(estimate ?? "", { exact: true }),
      ).toBeInTheDocument();
      expect(row).toHaveTextContent(`First action: ${firstAction ?? ""}`);
    });
    expect(
      within(panel).getByRole("link", { name: "+2 more" }),
    ).toHaveAttribute("href", "/tasks?status=today");
    unmount();
  }
});

test("[P0-23][FR-1.2] T-P0-23-04 AI tasks show no estimate", async () => {
  const items = [
    makeTask({
      project_id: ACME,
      title: "Draft the outline",
      label: "ai",
      estimate_minutes: null,
      first_action: "Read the brief",
      status: "today",
    }),
    makeTask({
      project_id: ACME,
      title: "Tag the photos",
      label: "ai",
      estimate_minutes: 30,
      first_action: "Open the folder",
      status: "today",
    }),
    makeTask({
      project_id: DOGFOOD,
      title: "Plan the week",
      label: "human",
      estimate_minutes: 30,
      first_action: "Open the calendar",
      status: "today",
    }),
  ];
  for (const viewport of VIEWPORTS) {
    const { unmount } = await renderWithRouter(
      <TodayPanel items={items} total={3} projectNames={PROJECT_NAMES} />,
      { viewport },
    );
    const [outline, photos, week] = rowsOf(
      screen.getByRole("region", { name: "Today" }),
    );
    expect(outline).toHaveTextContent("AI");
    expect(outline).not.toHaveTextContent(/\d+\s*(min|h)\b/);
    expect(photos).not.toHaveTextContent(/\d+\s*(min|h)\b/);
    expect(week).toHaveTextContent("30 min");
    expect(screen.queryByRole("link", { name: /more/ })).toBeNull();
    unmount();
  }
});

test("[P0-23][FR-1.2][UX 5] T-P0-23-05 a task without a first action says so", async () => {
  const items = [
    makeTask({
      project_id: ACME,
      title: "Tidy the drive",
      label: "human",
      estimate_minutes: 20,
      first_action: null,
      status: "today",
    }),
  ];
  for (const viewport of VIEWPORTS) {
    const { unmount } = await renderWithRouter(
      <TodayPanel items={items} total={1} projectNames={PROJECT_NAMES} />,
      { viewport },
    );
    const [row] = rowsOf(screen.getByRole("region", { name: "Today" }));
    expect(row).toHaveTextContent("No first action yet");
    expect(row).not.toHaveTextContent("First action:");
    unmount();
  }
});

test("[P0-23][FR-1.2] a pending label says so", async () => {
  const items = [
    makeTask({
      project_id: ACME,
      title: "Sort the inbox",
      label: null,
      estimate_minutes: 20,
      first_action: "Open the inbox",
      status: "today",
    }),
  ];
  await renderWithRouter(
    <TodayPanel items={items} total={1} projectNames={PROJECT_NAMES} />,
  );
  const [row] = rowsOf(screen.getByRole("region", { name: "Today" }));
  expect(row).toHaveTextContent("No label yet");
  expect(row).not.toHaveTextContent("20 min");
});

// --- The published plan (P1-11, FR-1.2, FR-4.3, J1) ------------------------------------

const MONDAY = "2026-03-09";

function planRows(): HTMLElement[] {
  return rowsOf(screen.getByRole("region", { name: "Today" }));
}

async function renderPlan(
  handlers: ReturnType<typeof planHandlers>,
  viewport?: Viewport,
) {
  server.use(...handlers);
  const result = await renderWithRouter(
    <TodayPanel items={[]} total={0} projectNames={{}} planDay={MONDAY} />,
    viewport ? { viewport } : {},
  );
  await screen.findByText("Send logo drafts");
  return result;
}

test.fails(
  "[P1-11][FR-1.2] items show project, label, estimate, first action, reason, block",
  async () => {
    const expected = [
      [
        "Send logo drafts",
        "Acme site",
        "Human",
        "1 h",
        "Due today",
        "09:00–10:00",
      ],
      [
        "Book the venue",
        "Tumnis dogfood",
        "Hybrid",
        "30 min",
        "Client is waiting",
        "10:00–10:30",
      ],
      [
        "Draft the outline",
        "Acme site",
        "AI",
        null,
        "Runs while you work",
        "Runs anytime",
      ],
      [
        "Write the proposal",
        "Acme site",
        "Human",
        "1 h",
        "Rolled over twice",
        "13:00–14:00",
      ],
    ] as const;
    for (const viewport of VIEWPORTS) {
      const { unmount } = await renderPlan(
        planHandlers(new Recorder()),
        viewport,
      );
      const rows = planRows();
      expect(rows).toHaveLength(4);
      rows.forEach((row, i) => {
        const [title, project, label, estimate, reason, block] =
          expected[i] ?? [];
        const at = within(row);
        expect(at.getByText(title ?? "", { exact: true })).toBeInTheDocument();
        expect(at.getByTestId("plan-project")).toHaveTextContent(project ?? "");
        expect(at.getByTestId("label-chip")).toHaveTextContent(label ?? "");
        if (estimate === null) {
          expect(at.queryByTestId("estimate-chip")).toBeNull();
        } else {
          expect(at.getByTestId("estimate-chip")).toHaveTextContent(
            estimate ?? "",
          );
        }
        expect(at.getByTestId("first-action")).toHaveTextContent(
          `First action: First step ${String(i + 1)}`,
        );
        expect(at.getByTestId("plan-reason")).toHaveTextContent(reason ?? "");
        expect(at.getByTestId("plan-block")).toHaveTextContent(block ?? "");
        expect(row).toHaveAttribute("data-state", "proposed");
      });
      unmount();
    }
  },
);

test.fails("[P1-11][J1] keyboard accept, swap, remove", async () => {
  const recorder = new Recorder();
  const plan = mondayPlan();
  const { user, unmount } = await renderPlan(planHandlers(recorder, plan));
  const [first, second, third] = plan.items;

  // `a` accepts the focused item: one POST, and the row shows it at once.
  const rows = planRows();
  rows[0]?.focus();
  await user.keyboard("a");
  await waitFor(() => {
    expect(planRows()[0]).toHaveAttribute("data-state", "accepted");
  });

  // `s` opens the swap picker; picking an alternative swaps it in, in place.
  planRows()[2]?.focus();
  await user.keyboard("s");
  const picker = await screen.findByRole("dialog", { name: "Swap" });
  await user.click(
    await within(picker).findByRole("option", { name: /Call the printer/ }),
  );
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "Swap" })).toBeNull();
  });
  await waitFor(() => {
    expect(planRows()[2]).toHaveTextContent("Call the printer");
  });

  // `x` removes the focused item: it leaves the panel.
  planRows()[1]?.focus();
  await user.keyboard("x");
  await waitFor(() => {
    expect(planRows()).toHaveLength(3);
  });
  expect(screen.queryByText("Book the venue")).toBeNull();

  const base = `/v1/plan/${MONDAY}/items`;
  expect(recorder.writes()).toEqual([
    `POST ${base}/${first?.task_id ?? ""}/accept`,
    `POST ${base}/${third?.task_id ?? ""}/swap`,
    `POST ${base}/${second?.task_id ?? ""}/remove`,
  ]);
  const swap = recorder.sent.find((s) => s.path.endsWith("/swap"));
  expect(swap?.body).toEqual({ with_task_id: ALTERNATES[0]?.id });
  for (const sent of recorder.sent.filter((s) => s.method === "POST")) {
    expect(sent.idempotencyKey).toBeTruthy();
  }

  // A 409 rolls the optimistic accept back.
  unmount();
  const conflicted = new Recorder();
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const again = await renderPlan(
    planHandlers(conflicted, mondayPlan(), { fail: "accept", gate }),
  );
  planRows()[0]?.focus();
  await again.user.keyboard("a");
  // Optimistic: accepted before the server answers.
  await waitFor(() => {
    expect(planRows()[0]).toHaveAttribute("data-state", "accepted");
  });
  release();
  await waitFor(() => {
    expect(planRows()[0]).toHaveAttribute("data-state", "proposed");
  });
  expect(conflicted.writes()).toHaveLength(1);
});

test.fails("[P1-11][FR-4.3] fallback notice and blocked badge", async () => {
  const plan = mondayPlan({
    source: "fallback",
    notice: "agent_offline",
    items: mondayPlan().items.map((item, i) =>
      i === 1 ? { ...item, blocked: true, status: "waiting_on_human" } : item,
    ),
  });
  for (const viewport of VIEWPORTS) {
    const { unmount } = await renderPlan(
      planHandlers(new Recorder(), plan),
      viewport,
    );
    const panel = screen.getByRole("region", { name: "Today" });
    expect(panel).toHaveTextContent(
      "Planned by due date: your planning agent is offline",
    );
    const [first, blocked] = planRows();
    expect(blocked).toHaveTextContent("Waiting on you");
    expect(first).not.toHaveTextContent("Waiting on you");
    unmount();
  }
});
