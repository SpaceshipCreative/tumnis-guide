// The service worker's notification click (P4-05, FR-8.3): a push carries a review URL;
// the click opens it only as a same-origin path whose params pass the /review route's
// zod schema, and anything else lands on /review (P0-22: an invalid deep link lands on a
// valid default). A focus push carries "/" (the dashboard and its focus bar). An open
// window is focused and navigated rather than a new one opened.
import { describe, expect, test } from "vitest";

import { openOrFocus, safeReviewUrl } from "./sw";
import { fakeClients, windowClient } from "./test/sw";

const ORIGIN = "https://tumnis.example.com";
const ITEM = "0199aa00-0000-7000-8000-0000000004a1";

describe("notification click", () => {
  test("[P4-05][FR-8.3] T-P4-05-08 click opens the validated review url", async () => {
    // Valid links keep exactly the review route's params.
    expect(safeReviewUrl(`/review?kind=approval&item=${ITEM}`, ORIGIN)).toBe(
      `/review?kind=approval&item=${ITEM}`,
    );
    expect(
      safeReviewUrl(`${ORIGIN}/review?kind=question&item=${ITEM}`, ORIGIN),
    ).toBe(`/review?kind=question&item=${ITEM}`);
    expect(safeReviewUrl(`/review?item=${ITEM}&extra=1`, ORIGIN)).toBe(
      `/review?item=${ITEM}`,
    );
    expect(safeReviewUrl("/review?kind=Not%20a%20kind", ORIGIN)).toBe(
      "/review",
    );
    expect(safeReviewUrl("/review?item=not-a-uuid", ORIGIN)).toBe("/review");
    // A focus push opens the dashboard, where the focus bar is; no params kept.
    expect(safeReviewUrl("/", ORIGIN)).toBe("/");
    expect(safeReviewUrl("/?next=//evil.example.com", ORIGIN)).toBe("/");
    // Invalid, cross-origin or other paths become /review.
    for (const bad of [
      `https://evil.example.com/review?kind=approval&item=${ITEM}`,
      `//evil.example.com/review?kind=approval&item=${ITEM}`,
      "javascript:alert(1)",
      "/settings/keys",
      "/review/../settings",
      "",
      undefined,
      42,
      { url: "/review" },
    ]) {
      expect(safeReviewUrl(bad, ORIGIN), JSON.stringify(bad)).toBe("/review");
    }

    // An open window: focused and navigated, no new window.
    const open = windowClient(`${ORIGIN}/projects`);
    const clients = fakeClients([open]);
    await openOrFocus(`/review?kind=approval&item=${ITEM}`, clients, ORIGIN);
    expect(open.navigate).toHaveBeenCalledWith(
      `${ORIGIN}/review?kind=approval&item=${ITEM}`,
    );
    expect(open.focus).toHaveBeenCalledOnce();
    expect(clients.openWindow).not.toHaveBeenCalled();

    // No window: one is opened on the link.
    const none = fakeClients([]);
    await openOrFocus("/review", none, ORIGIN);
    expect(none.openWindow).toHaveBeenCalledWith(`${ORIGIN}/review`);
  });
});
