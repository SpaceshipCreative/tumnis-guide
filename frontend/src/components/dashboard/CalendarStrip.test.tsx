// The dashboard's calendar strip (P1-10, FR-1.3): today's working window in the workspace
// timezone, the events from every account and the free blocks between them, with a marker
// at the current time.
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../../test/msw/server";
import { DAY_CALENDAR, dayCalendar } from "../../test/msw/planning";
import { renderWithProviders, type Viewport } from "../../test/render";
import { CalendarStrip } from "./CalendarStrip";

const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];
const DAY = "2026-03-09";
// 11:15 EDT: a quarter of the way through the 09:00 to 18:00 window.
const NOW = new Date("2026-03-09T15:15:00Z");

afterEach(() => {
  vi.useRealTimers();
});

test("[P1-10][FR-1.3] T-P1-10-12 free blocks highlighted", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
  for (const viewport of VIEWPORTS) {
    const days: string[] = [];
    server.use(dayCalendar(DAY_CALENDAR, (day) => days.push(day)));
    renderWithProviders(<CalendarStrip day={DAY} />, { viewport });

    const strip = await screen.findByRole("region", {
      name: "Today's calendar",
    });
    expect(days).toEqual([DAY]);

    // Free blocks: listed with their local times and lengths, highlighted.
    const free = within(
      within(strip).getByRole("list", { name: "Free blocks" }),
    ).getAllByRole("listitem");
    expect(free.map((item) => item.getAttribute("aria-label"))).toEqual([
      "Free 09:00 to 11:00, 120 minutes",
      "Free 11:30 to 13:00, 90 minutes",
      "Free 14:00 to 18:00, 240 minutes",
    ]);
    for (const item of free) expect(item).toHaveAttribute("data-kind", "free");
    expect(free[0]?.style.left).toBe("0%");

    // Events: busy ones block time; a free (transparent) one is shown as not blocking.
    const events = within(
      within(strip).getByRole("list", { name: "Events" }),
    ).getAllByRole("listitem");
    expect(events.map((item) => item.getAttribute("aria-label"))).toEqual([
      "Busy: Standup, 11:00 to 11:30, avery@example.com",
      "Not blocking: Optional lunch, 12:00 to 13:00, avery@example.com",
      "Busy: Design review, 13:00 to 14:00, blake@example.org",
    ]);
    expect(events.map((item) => item.getAttribute("data-kind"))).toEqual([
      "busy",
      "tentative",
      "busy",
    ]);

    // The now marker sits at the (fake) current time.
    const now = within(strip).getByRole("img", { name: "Now 11:15" });
    expect(now.style.left).toBe("25%");

    cleanup();
  }
});
