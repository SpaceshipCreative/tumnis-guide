// The review queue (P0-22 route; the screen arrives with P1-13). `kind` is a string
// checked against the registry (R-05), not a closed enum: an unknown kind is dropped.
import { createFileRoute, redirect } from "@tanstack/react-router";
import * as z from "zod";

import { Placeholder } from "../components/pages/Placeholder";
import { reviewKindsOptions } from "../lib/review-kinds";

export const reviewSearch = z.object({
  kind: z
    .string()
    .regex(/^[a-z][a-z0-9_]{2,40}$/)
    .optional()
    .catch(undefined),
  item: z.uuid().optional().catch(undefined),
});

export const Route = createFileRoute("/review")({
  validateSearch: reviewSearch,
  beforeLoad: async ({ search, context }) => {
    if (search.kind === undefined) return;
    const kinds = await context.queryClient.query(reviewKindsOptions());
    if (kinds !== null && !kinds.includes(search.kind)) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
      throw redirect({
        to: "/review",
        search: search.item === undefined ? {} : { item: search.item },
        replace: true,
      });
    }
  },
  component: ReviewPage,
});

function ReviewPage() {
  return <Placeholder title="Review" />;
}
