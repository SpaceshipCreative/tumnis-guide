// Every generated query is either refreshed by the live socket or declared not live
// (P0-22, ADR-0003, R-19). A new GET route fails here until it is mapped.
import { expect, test } from "vitest";

import * as generated from "../api/@tanstack/react-query.gen";
import { LIVE_MAP, NOT_LIVE } from "./live-map";

// `<opId>QueryKey` for each query op (infinite variants share the op's `_id`).
function generatedQueryOps(): Set<string> {
  return new Set(
    Object.keys(generated)
      .filter((name) => name.endsWith("QueryKey"))
      .filter((name) => !name.endsWith("InfiniteQueryKey"))
      .map((name) => name.slice(0, -"QueryKey".length)),
  );
}

test.fails(
  "[P0-22][ADR-0003] T-P0-22-11 every generated query op is mapped or declared not live",
  () => {
    const ops = generatedQueryOps();
    const mapped = new Set(
      Object.values(LIVE_MAP).flatMap((entry) => [
        ...entry.details,
        ...entry.lists,
      ]),
    );
    const notLive = new Set<string>(NOT_LIVE);

    expect(ops.size).toBeGreaterThan(0);
    const unmapped = [...ops].filter(
      (op) => !mapped.has(op) && !notLive.has(op),
    );
    expect(unmapped).toEqual([]);
    const unknown = [...mapped, ...notLive].filter((op) => !ops.has(op));
    expect(unknown).toEqual([]);
    const both = [...mapped].filter((op) => notLive.has(op));
    expect(both).toEqual([]);
  },
);
