// Snooze ends (P1-13, R-04): an hour, three hours, or the next working day's start in
// the workspace timezone (weekends skipped, 09:00 without the weekday's own hours).
import { describe, expect, test } from "vitest";

import { localToUtc, nextWorkingDayStart, snoozeUntil } from "./time";

const NY = "America/New_York";
const WEEKDAYS = [0, 1, 2, 3, 4].map((weekday) => ({
  weekday,
  start: "08:30",
}));

describe("[P1-13] snoozeUntil", () => {
  const monday = new Date("2026-03-09T14:00:00Z"); // 10:00 in New York

  test("one and three hours from now", () => {
    expect(snoozeUntil("1h", monday, [], NY).toISOString()).toBe(
      "2026-03-09T15:00:00.000Z",
    );
    expect(snoozeUntil("3h", monday, [], NY).toISOString()).toBe(
      "2026-03-09T17:00:00.000Z",
    );
  });

  test("tomorrow is the next weekday's own start", () => {
    expect(snoozeUntil("tomorrow", monday, WEEKDAYS, NY).toISOString()).toBe(
      "2026-03-10T12:30:00.000Z", // Tuesday 08:30 EDT
    );
  });

  test("a weekday without its own hours starts at 09:00", () => {
    expect(nextWorkingDayStart(monday, [], NY).toISOString()).toBe(
      "2026-03-10T13:00:00.000Z",
    );
  });

  test("Friday evening skips the weekend", () => {
    const friday = new Date("2026-03-13T23:30:00Z"); // 19:30 in New York
    expect(nextWorkingDayStart(friday, WEEKDAYS, NY).toISOString()).toBe(
      "2026-03-16T12:30:00.000Z", // Monday 08:30 EDT
    );
  });

  test("the local day decides, not the UTC one", () => {
    const lateSunday = new Date("2026-03-16T03:00:00Z"); // Sunday 23:00 in New York
    expect(nextWorkingDayStart(lateSunday, [], NY).toISOString()).toBe(
      "2026-03-16T13:00:00.000Z", // Monday 09:00 EDT
    );
  });
});

describe("[P1-13] localToUtc", () => {
  test("standard and daylight time", () => {
    const day = { year: 2026, month: 1, day: 5 };
    expect(localToUtc(day, "09:00", NY).toISOString()).toBe(
      "2026-01-05T14:00:00.000Z",
    );
    expect(
      localToUtc({ year: 2026, month: 7, day: 6 }, "09:00", NY).toISOString(),
    ).toBe("2026-07-06T13:00:00.000Z");
  });

  test("a time the spring gap skips moves forward by the gap", () => {
    const springForward = { year: 2026, month: 3, day: 8 };
    expect(localToUtc(springForward, "02:30", NY).toISOString()).toBe(
      "2026-03-08T07:30:00.000Z", // 03:30 EDT
    );
  });

  test("an ambiguous autumn time takes its first occurrence", () => {
    const fallBack = { year: 2026, month: 11, day: 1 };
    expect(localToUtc(fallBack, "01:30", NY).toISOString()).toBe(
      "2026-11-01T05:30:00.000Z", // 01:30 EDT
    );
  });

  test("UTC has no offset", () => {
    expect(
      localToUtc(
        { year: 2026, month: 3, day: 9 },
        "09:00",
        "UTC",
      ).toISOString(),
    ).toBe("2026-03-09T09:00:00.000Z");
  });
});
