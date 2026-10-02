// The generated client (P0-22) hands a 429's Retry-After to the reads' retry policy
// (lib/retry.ts): the problem body has no header, so the client adds it to the error.
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { planningGetPlan } from "../api/sdk.gen";
import { server } from "../test/msw/server";
import { retryDelay } from "./retry";

const LIMITED = {
  type: "https://tumnis.dev/problems/rate_limited",
  title: "Rate limited",
  status: 429,
  code: "rate_limited",
};

async function planError(): Promise<unknown> {
  try {
    await planningGetPlan({
      path: { day: "2026-03-09" },
      throwOnError: true,
    });
  } catch (error) {
    return error;
  }
  throw new Error("the read did not fail");
}

test("a 429's Retry-After rides on the error the client throws", async () => {
  server.use(
    http.get("*/v1/plan/:day", () =>
      HttpResponse.json(LIMITED, {
        status: 429,
        headers: { "Retry-After": "3" },
      }),
    ),
  );
  const error = await planError();
  expect(error).toMatchObject(LIMITED);
  expect(retryDelay(0, error)).toBe(3_000);
});

test("other errors come through as the problem the server sent", async () => {
  const missing = { title: "Not found", status: 404, code: "not_found" };
  server.use(
    http.get("*/v1/plan/:day", () =>
      HttpResponse.json(missing, {
        status: 404,
        headers: { "Retry-After": "3" },
      }),
    ),
  );
  expect(await planError()).toEqual(missing);
});
