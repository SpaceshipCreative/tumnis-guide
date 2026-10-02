// A loader's read never waits on the network (P0-25, FR-3.10): offline it fails at once and
// the page renders what it has; online it fills the cache like any other read.
import { QueryClient } from "@tanstack/react-query";
import { onlineManager } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";

import { loaderRead } from "./loader";

afterEach(() => {
  onlineManager.setOnline(true);
});

test("a read that fails settles once, without retrying", async () => {
  const client = new QueryClient();
  const queryFn = vi.fn(() => Promise.reject(new Error("no network")));
  await expect(
    loaderRead(client, { queryKey: ["read"], queryFn }),
  ).resolves.toBeUndefined();
  expect(queryFn).toHaveBeenCalledTimes(1);
});

test("a read is not paused while the device is offline", async () => {
  onlineManager.setOnline(false);
  const client = new QueryClient();
  const queryFn = vi.fn(() => Promise.reject(new Error("no network")));
  await expect(
    loaderRead(client, { queryKey: ["read"], queryFn }),
  ).resolves.toBeUndefined();
  expect(queryFn).toHaveBeenCalledTimes(1);
});

test("a read that succeeds fills the cache", async () => {
  const client = new QueryClient();
  await loaderRead(client, { queryKey: ["read"], queryFn: () => 42 });
  expect(client.getQueryData(["read"])).toBe(42);
});

/** An error carrying a problem's status and code, as the reads' errors do. */
function problem(status: number, code: string): Error {
  return Object.assign(new Error(code), { status, code });
}

// The J1 A1.2 flake: the reload's reads ran into the per-principal rate limit and the
// plan's 429 stayed in the cache, so the Today panel showed the wrong list.
test("a read refused with 429 is tried again after its Retry-After", async () => {
  vi.useFakeTimers();
  try {
    const client = new QueryClient();
    const queryFn = vi
      .fn<() => Promise<number>>()
      .mockRejectedValueOnce(problem(429, "rate_limited"))
      .mockResolvedValueOnce(42);
    const read = loaderRead(client, { queryKey: ["read"], queryFn });
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(read).resolves.toBe(42);
    expect(queryFn).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(["read"])).toBe(42);
  } finally {
    vi.useRealTimers();
  }
});

test("a read refused with 429 every time settles after two more tries", async () => {
  vi.useFakeTimers();
  try {
    const client = new QueryClient();
    const queryFn = vi.fn(() => Promise.reject(problem(429, "rate_limited")));
    const read = loaderRead(client, { queryKey: ["read"], queryFn });
    await vi.advanceTimersByTimeAsync(5_000);
    await expect(read).resolves.toBeUndefined();
    expect(queryFn).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
  }
});

test("a read refused with another 4xx is not tried again", async () => {
  const client = new QueryClient();
  const queryFn = vi.fn(() => Promise.reject(problem(404, "not_found")));
  await expect(
    loaderRead(client, { queryKey: ["read"], queryFn }),
  ).resolves.toBeUndefined();
  expect(queryFn).toHaveBeenCalledTimes(1);
});
