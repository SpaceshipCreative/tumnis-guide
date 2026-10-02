// A 429 is the one 4xx worth asking again (the J1 A1.2 flake): the server's per-principal
// bucket refills within a second, so a read refused for going too fast is tried again after
// its Retry-After, a bounded number of times. Every other 4xx stays final.
import { expect, test } from "vitest";

import {
  RATE_LIMIT_RETRIES,
  RETRY_AFTER_CAP_MS,
  readRetryAfter,
  retryDelay,
  retryRateLimited,
  withRetryAfter,
} from "./retry";

const LIMITED = { status: 429, code: "rate_limited" };

test("a 429 is tried again, at most RATE_LIMIT_RETRIES times", () => {
  expect(RATE_LIMIT_RETRIES).toBe(2);
  expect(retryRateLimited(0, LIMITED)).toBe(true);
  expect(retryRateLimited(1, LIMITED)).toBe(true);
  expect(retryRateLimited(2, LIMITED)).toBe(false);
});

test("other answers and failures are not tried again", () => {
  expect(retryRateLimited(0, { status: 404, code: "not_found" })).toBe(false);
  expect(retryRateLimited(0, { status: 409, code: "conflict" })).toBe(false);
  expect(retryRateLimited(0, new Error("no network"))).toBe(false);
  expect(retryRateLimited(0, "Too Many Requests")).toBe(false);
});

test("a 429 waits its Retry-After, capped; without one it waits a second", () => {
  expect(retryDelay(0, withRetryAfter(LIMITED, "2"))).toBe(2_000);
  expect(retryDelay(0, withRetryAfter(LIMITED, "0"))).toBe(0);
  expect(retryDelay(0, withRetryAfter(LIMITED, "120"))).toBe(
    RETRY_AFTER_CAP_MS,
  );
  expect(RETRY_AFTER_CAP_MS).toBe(5_000);
  expect(retryDelay(0, LIMITED)).toBe(1_000);
  expect(retryDelay(1, LIMITED)).toBe(1_000);
});

test("other failures keep Query's own backoff", () => {
  expect(retryDelay(0, new Error("no network"))).toBe(1_000);
  expect(retryDelay(1, new Error("no network"))).toBe(2_000);
  expect(retryDelay(10, { status: 503 })).toBe(30_000);
});

test("Retry-After is read as seconds or an HTTP date; anything else is ignored", () => {
  const now = Date.parse("2026-03-09T12:30:00Z");
  expect(readRetryAfter("3", now)).toBe(3_000);
  expect(readRetryAfter("Mon, 09 Mar 2026 12:30:04 GMT", now)).toBe(4_000);
  expect(readRetryAfter("Mon, 09 Mar 2026 12:29:00 GMT", now)).toBe(0);
  expect(readRetryAfter(null, now)).toBeUndefined();
  expect(readRetryAfter("soon", now)).toBeUndefined();
  expect(readRetryAfter("-1", now)).toBeUndefined();
});

test("withRetryAfter keeps the problem's fields and leaves other errors alone", () => {
  const error = withRetryAfter({ ...LIMITED, detail: "slow down" }, "1");
  expect(error).toMatchObject({ ...LIMITED, detail: "slow down" });
  expect(retryDelay(0, error)).toBe(1_000);
  expect(withRetryAfter(LIMITED, null)).toEqual(LIMITED);
});
