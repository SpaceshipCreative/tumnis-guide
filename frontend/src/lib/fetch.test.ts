import { afterEach, describe, expect, test, vi } from "vitest";

import { apiFetch, readCookie } from "./fetch";

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(document, "cookie");
  });

  test("[P0-13][SEC-1] reads a cookie by name", () => {
    expect(readCookie("b", "a=1; b=two%20words; c=3")).toBe("two words");
    expect(readCookie("missing", "a=1")).toBeUndefined();
  });

  test("[P0-13][SEC-1] writes send the CSRF cookie as X-CSRF-Token and an Idempotency-Key", async () => {
    // jsdom refuses a __Host- cookie on http://; the browser sets it over https.
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "other=1; __Host-tumnis_csrf=token-123",
    });
    const spy = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    vi.stubGlobal("fetch", spy);
    await apiFetch("/v1/auth/logout", { method: "post" });
    await apiFetch("/v1/auth/sessions");
    const [write, read] = spy.mock.calls as unknown as [string, RequestInit][];
    const writeHeaders = new Headers(write?.[1].headers);
    expect(write?.[1].method).toBe("POST");
    expect(writeHeaders.get("X-CSRF-Token")).toBe("token-123");
    expect(writeHeaders.get("Idempotency-Key")).toBeTruthy();
    const readHeaders = new Headers(read?.[1].headers);
    expect(readHeaders.get("X-CSRF-Token")).toBeNull();
    expect(readHeaders.get("Idempotency-Key")).toBeNull();
  });
});
