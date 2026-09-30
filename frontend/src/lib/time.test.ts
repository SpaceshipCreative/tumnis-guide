// Slot arithmetic for the Calendar view (P1-12): free time minus planned blocks, 15-minute
// marks, and the starts where a task's estimate fits.
import { expect, test } from "vitest";

import {
  addDays,
  dayName,
  fittingStarts,
  isMonday,
  mondayOf,
  slotStarts,
  spanText,
  subtract,
} from "./time";

const NY = "America/New_York";

test("[P1-12][FR-2.6] subtract cuts taken spans out of free time", () => {
  const free = [
    { start: "2026-03-10T17:00:00.000Z", end: "2026-03-10T22:00:00.000Z" },
  ];
  const taken = [
    { start: "2026-03-10T19:00:00.000Z", end: "2026-03-10T19:30:00.000Z" },
    { start: "2026-03-10T21:30:00.000Z", end: "2026-03-10T23:00:00.000Z" },
  ];
  expect(subtract(free, taken).map((s) => spanText(s, NY))).toEqual([
    "13:00 to 15:00",
    "15:30 to 17:30",
  ]);
  expect(subtract(free, [])).toEqual(free);
});

test("[P1-12][FR-2.6] slots start on 15-minute marks and never in the past", () => {
  const span = { start: "2026-03-10T14:05:00Z", end: "2026-03-10T15:00:00Z" };
  expect(slotStarts(span).map((s) => s.slice(11, 16))).toEqual([
    "14:15",
    "14:30",
    "14:45",
  ]);
  expect(
    slotStarts(span, new Date("2026-03-10T14:31:00Z")).map((s) =>
      s.slice(11, 16),
    ),
  ).toEqual(["14:45"]);
});

test("[P1-12][FR-2.6] a 45-minute task fits only where 45 minutes are free", () => {
  const spans = [
    { start: "2026-03-10T14:00:00Z", end: "2026-03-10T15:00:00Z" },
    { start: "2026-03-10T16:00:00Z", end: "2026-03-10T16:30:00Z" },
  ];
  expect(fittingStarts(spans, 45).map((s) => s.slice(11, 16))).toEqual([
    "14:00",
    "14:15",
  ]);
});

test("[P1-12][FR-2.6] week dates", () => {
  expect(dayName("2026-03-10")).toBe("Tuesday 10 March");
  expect(addDays("2026-03-09", 7)).toBe("2026-03-16");
  expect(isMonday("2026-03-09")).toBe(true);
  expect(isMonday("2026-03-10")).toBe(false);
  expect(isMonday("not-a-day")).toBe(false);
  // Sunday 15 March, 23:30 in New York is still the week of Monday 9 March.
  expect(mondayOf(new Date("2026-03-16T03:30:00Z"), NY)).toBe("2026-03-09");
  expect(mondayOf(new Date("2026-03-16T04:30:00Z"), NY)).toBe("2026-03-16");
});
