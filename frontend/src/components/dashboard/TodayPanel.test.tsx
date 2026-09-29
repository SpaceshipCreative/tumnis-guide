// The Today panel (P0-23, FR-1.2, UX 3, UX 5): at most five tasks in the server's order,
// each with its project, label, estimate (Human and Hybrid only) and first action, then
// "+N more" to the full Today list.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
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
