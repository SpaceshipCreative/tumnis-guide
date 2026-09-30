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
