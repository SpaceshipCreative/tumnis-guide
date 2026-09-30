// The project's Calendar view (P1-12, FR-2.6): a week of the project's meetings, due dates
// and planned blocks over the free time, from `GET /v1/plan/week/{monday}`. Dropping a
// task on a free slot, by pointer or by keyboard, schedules it with one
// `PATCH /v1/plan/{day}/items/{task_id}`; a drop on busy time sends nothing. On the phone
// the week is a day list and each task has a Schedule action with a slot picker. Messages
// go to the view's own live region, "Scheduling" (dnd-kit adds an unnamed one).
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { cardPoint, layOutWeek, pointAt } from "../../test/calendarLayout";
import { server } from "../../test/msw/server";
import { MONDAY, PROJECT_ID, TASK_45, WeekFake } from "../../test/msw/week";
import { renderWithProviders, type Viewport } from "../../test/render";
import { CalendarView } from "./CalendarView";

// Monday 9 March 2026, 08:00 in New York: the whole week is ahead.
const NOW = new Date("2026-03-09T12:00:00Z");
const DAYS = [
  "Monday 9 March",
  "Tuesday 10 March",
  "Wednesday 11 March",
  "Thursday 12 March",
  "Friday 13 March",
  "Saturday 14 March",
  "Sunday 15 March",
] as const;
const PATCH_TUESDAY = `PATCH /v1/plan/2026-03-10/items/${TASK_45}`;

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function setUp(viewport: Viewport) {
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
  layOutWeek();
  const fake = new WeekFake();
  server.use(...fake.handlers);
  const view = renderWithProviders(
    <CalendarView
      projectId={PROJECT_ID}
      week={MONDAY}
      onWeek={() => undefined}
    />,
    { viewport },
  );
  return { fake, ...view };
}

function labels(list: HTMLElement): (string | null)[] {
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.getAttribute("aria-label") ?? item.textContent);
}

function sentBlock(fake: WeekFake): { start: number; end: number } {
  const patch = fake.recorder.sent.find((s) => s.method === "PATCH");
  const body = patch?.body as { block_start: string; block_end: string };
  return {
    start: Date.parse(body.block_start),
    end: Date.parse(body.block_end),
  };
}

test("[P1-12][FR-2.6] T-P1-12-01 week shows meetings, due dates and planned blocks", async () => {
  const { fake } = setUp("laptop");
  const calendar = await screen.findByRole("region", { name: "Calendar" });
  expect(
    fake.recorder.sent.map((s) => `${s.method} ${s.path}${s.search}`),
  ).toEqual([`GET /v1/plan/week/${MONDAY}?project_id=${PROJECT_ID}`]);
  for (const name of DAYS) {
    expect(within(calendar).getByRole("region", { name })).toBeVisible();
  }

  const monday = within(calendar).getByRole("region", { name: DAYS[0] });
  expect(labels(within(monday).getByRole("list", { name: "Events" }))).toEqual([
    "Kickoff with Acme, 11:00 to 12:00",
  ]);

  const tuesday = within(calendar).getByRole("region", { name: DAYS[1] });
  // The project's meeting by title; someone else's is only busy time.
  expect(labels(within(tuesday).getByRole("list", { name: "Events" }))).toEqual(
    ["Acme standup, 09:00 to 10:00", "Busy, 12:00 to 13:00"],
  );
  expect(labels(within(tuesday).getByRole("list", { name: "Due" }))).toEqual([
    "Due: Send logo drafts",
  ]);
  expect(
    labels(within(tuesday).getByRole("list", { name: "Planned" })),
  ).toEqual(["Review the palette, 15:00 to 15:30"]);
  // Free time left after the planned block.
  expect(
    labels(within(tuesday).getByRole("list", { name: "Free time" })),
  ).toEqual([
    "Free 10:00 to 12:00",
    "Free 13:00 to 15:00",
    "Free 15:30 to 18:00",
  ]);

  const saturday = within(calendar).getByRole("region", { name: DAYS[5] });
  expect(within(saturday).getByText("No working hours")).toBeVisible();

  const toSchedule = within(calendar).getByRole("list", {
    name: "To schedule",
  });
  expect(
    within(toSchedule).getByRole("button", {
      name: "Draft the brand guide, 45 minutes",
    }),
  ).toBeVisible();
  expect(
    within(toSchedule).getByRole("button", {
      name: "Pick the type scale, 30 minutes",
    }),
  ).toBeVisible();
});

test("[P1-12][FR-2.6] T-P1-12-02 drop on free block sends one PATCH", async () => {
  const { fake, user } = setUp("laptop");
  const card = await screen.findByRole("button", {
    name: "Draft the brand guide, 45 minutes",
  });
  const from = cardPoint(0);
  const to = pointAt(1, "10:15"); // Tuesday, inside the free block 10:00 to 12:00
  await user.pointer([
    {
      keys: "[MouseLeft>]",
      target: card,
      coords: { clientX: from.x, clientY: from.y },
    },
    { coords: { clientX: to.x, clientY: to.y } },
    { keys: "[/MouseLeft]" },
  ]);

  // The card shows in the slot at once.
  const tuesday = screen.getByRole("region", { name: DAYS[1] });
  expect(
    await within(
      within(tuesday).getByRole("list", { name: "Planned" }),
    ).findByRole("listitem", {
      name: "Draft the brand guide, 10:15 to 11:00",
    }),
  ).toBeVisible();
  await waitFor(() => {
    expect(fake.writes()).toEqual([PATCH_TUESDAY]);
  });
  // 10:15 to 11:00 in New York (UTC-4): the task's 45 minutes from where it was dropped.
  expect(sentBlock(fake)).toEqual({
    start: Date.parse("2026-03-10T14:15:00Z"),
    end: Date.parse("2026-03-10T15:00:00Z"),
  });
  const patch = fake.recorder.sent.find((s) => s.method === "PATCH");
  expect(patch?.idempotencyKey).toBeTruthy();
  expect(
    within(screen.getByRole("list", { name: "To schedule" })).queryByText(
      /Draft the brand guide/,
    ),
  ).toBeNull();
  expect(fake.writes()).toHaveLength(1);
});

test("[P1-12][FR-2.6] T-P1-12-03 drop on busy block refused", async () => {
  const { fake, user } = setUp("laptop");
  const card = await screen.findByRole("button", {
    name: "Draft the brand guide, 45 minutes",
  });
  const from = cardPoint(0);
  const to = pointAt(1, "12:30"); // Tuesday, inside someone else's meeting
  await user.pointer([
    {
      keys: "[MouseLeft>]",
      target: card,
      coords: { clientX: from.x, clientY: from.y },
    },
    { coords: { clientX: to.x, clientY: to.y } },
    { keys: "[/MouseLeft]" },
  ]);

  expect(
    await screen.findByRole("status", { name: "Scheduling" }),
  ).toHaveTextContent("That time is busy");
  // The card is back in the list, and nothing was sent.
  expect(
    within(screen.getByRole("list", { name: "To schedule" })).getByRole(
      "button",
      {
        name: "Draft the brand guide, 45 minutes",
      },
    ),
  ).toBeVisible();
  expect(fake.writes()).toEqual([]);
});

test("[P1-12][UX 7] T-P1-12-04 keyboard scheduling", async () => {
  const { fake, user } = setUp("laptop");
  const card = await screen.findByRole("button", {
    name: "Draft the brand guide, 45 minutes",
  });
  card.focus();

  await user.keyboard(" "); // picks the card up at the first free slot ahead
  const status = screen.getByRole("status", { name: "Scheduling" });
  expect(status).toHaveTextContent("Picked up Draft the brand guide");
  expect(status).toHaveTextContent("Monday 9 March, 09:00");

  await user.keyboard("{ArrowRight}"); // the next day, at the first free slot from 09:00
  expect(status).toHaveTextContent("Tuesday 10 March, 10:00");
  await user.keyboard("{ArrowDown}"); // the next slot
  expect(status).toHaveTextContent("Tuesday 10 March, 10:15");
  expect(fake.writes()).toEqual([]);

  await user.keyboard(" "); // drops
  await waitFor(() => {
    expect(fake.writes()).toEqual([PATCH_TUESDAY]);
  });
  expect(sentBlock(fake)).toEqual({
    start: Date.parse("2026-03-10T14:15:00Z"),
    end: Date.parse("2026-03-10T15:00:00Z"),
  });
  const tuesday = screen.getByRole("region", { name: DAYS[1] });
  expect(
    await within(
      within(tuesday).getByRole("list", { name: "Planned" }),
    ).findByRole("listitem", { name: "Draft the brand guide, 10:15 to 11:00" }),
  ).toBeVisible();
  expect(fake.writes()).toHaveLength(1);
});

test("[P1-12][UX 11] T-P1-12-05 phone layout", async () => {
  const { fake, user } = setUp("phone");
  const calendar = await screen.findByRole("region", { name: "Calendar" });

  // The week is a list of days, one after another, with no drop grid.
  expect(
    within(calendar)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent),
  ).toEqual(DAYS);
  expect(calendar.querySelectorAll("[data-slot]")).toHaveLength(0);
  const tuesday = within(calendar).getByRole("region", { name: DAYS[1] });
  expect(labels(within(tuesday).getByRole("list", { name: "Events" }))).toEqual(
    ["Acme standup, 09:00 to 10:00", "Busy, 12:00 to 13:00"],
  );

  // Each task to schedule has a Schedule action.
  const toSchedule = within(calendar).getByRole("list", {
    name: "To schedule",
  });
  const items = within(toSchedule).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  const [first] = items as [HTMLElement, HTMLElement];
  for (const item of items) {
    expect(
      within(item).getByRole("button", { name: "Schedule" }),
    ).toBeVisible();
  }

  // It opens a slot picker: the starts where the 45 minutes fit in free time.
  await user.click(within(first).getByRole("button", { name: "Schedule" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Schedule Draft the brand guide",
  });
  const slots = within(dialog).getByRole("group", { name: DAYS[1] });
  expect(
    within(slots).getByRole("button", { name: "10:00 to 10:45" }),
  ).toBeVisible();
  expect(
    within(slots).queryByRole("button", { name: "11:30 to 12:15" }),
  ).toBeNull(); // meeting
  expect(
    within(slots).queryByRole("button", { name: "14:30 to 15:15" }),
  ).toBeNull(); // planned
  expect(within(dialog).queryByRole("group", { name: DAYS[5] })).toBeNull(); // no hours

  await user.click(
    within(slots).getByRole("button", { name: "10:15 to 11:00" }),
  );
  await waitFor(() => {
    expect(fake.writes()).toEqual([PATCH_TUESDAY]);
  });
  expect(sentBlock(fake)).toEqual({
    start: Date.parse("2026-03-10T14:15:00Z"),
    end: Date.parse("2026-03-10T15:00:00Z"),
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});
