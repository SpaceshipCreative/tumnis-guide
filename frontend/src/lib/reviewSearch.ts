// The /review route's search params (P0-22 route, P1-13 screen), shared with the service
// worker (P4-05), which keeps exactly these when a push is clicked. `kind` is a string
// checked against the registry (R-05), not a closed enum; `item` names the item that
// takes focus first. An invalid value is dropped, never an error (P0-22).
import * as z from "zod";

export const reviewSearch = z.object({
  kind: z
    .string()
    .regex(/^[a-z][a-z0-9_]{2,40}$/)
    .optional()
    .catch(undefined),
  item: z.uuid().optional().catch(undefined),
});
