// "Done recently" without formatting every date (PERF-2): the shortcut on elapsed time
// gives the same answer as counting local calendar days in the workspace timezone, for
// any instant, any zone (including the far offsets and a DST change) and either side of
// the seven-day edge.
import fc from "fast-check";
import { expect, test } from "vitest";

import { DONE_RECENT_DAYS, doneRecently, localDaysBetween } from "./grouping";

const ZONES = [
  "UTC",
  "America/New_York",
  "Europe/London",
  "Australia/Lord_Howe",
  "Pacific/Kiritimati", // UTC+14
  "Etc/GMT+12", // UTC-12
  "Asia/Kathmandu",
];
const DAY = 24 * 60 * 60 * 1000;

test("[P0-29][PERF-2] doneRecently matches counting local days", () => {
  fc.assert(
    fc.property(
      fc.constantFrom(...ZONES),
      // A `now` across several years, DST changes included.
      fc.integer({ min: Date.UTC(2025, 0, 1), max: Date.UTC(2028, 0, 1) }),
      // Completed from 20 days before to 2 days after (clock skew).
      fc.integer({ min: -2 * DAY, max: 20 * DAY }),
      (tz, nowMs, ago) => {
        const now = new Date(nowMs);
        const completed = new Date(nowMs - ago).toISOString();
        expect(doneRecently(completed, now, tz)).toBe(
          localDaysBetween(completed, now, tz) < DONE_RECENT_DAYS,
        );
      },
    ),
    { numRuns: 2000 },
  );
});

test("[P0-29][PERF-2] doneRecently at the seven-day edge in New York", () => {
  const tz = "America/New_York";
  const now = new Date("2026-03-09T16:00:00Z"); // Monday 12:00 EDT, a day after DST began
  // Six local days back is recent; seven is not, whatever the hour.
  expect(doneRecently("2026-03-03T05:00:00Z", now, tz)).toBe(true); // Tue 00:00 EST
  expect(doneRecently("2026-03-03T04:59:59Z", now, tz)).toBe(false); // Mon 23:59 EST
  expect(doneRecently("2026-03-09T15:00:00Z", now, tz)).toBe(true);
  expect(doneRecently("2026-02-01T12:00:00Z", now, tz)).toBe(false);
});
