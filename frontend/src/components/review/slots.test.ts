// The provisioning_failed slot (P1-13 with P1-06, Scott decision 29): the provision
// workflow may leave `error` empty and give only its `error_code`, so the summary falls
// back to the code. Every fixture is synthetic.
import { expect, test } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { slotFor } from "./slots";

const slot = slotFor("provisioning_failed");

test("[P1-13][FR-6.1] provisioning_failed summary shows the error", () => {
  const item = makeReviewItem({
    kind: "provisioning_failed",
    payload: { project_id: "p", error: "timeout", mode: "create" },
  });
  expect(slot.summary(item)).toBe(
    "Provisioning failed: timeout. Accept retries it.",
  );
});

test("[P1-13][FR-6.1] provisioning_failed summary falls back to the error code", () => {
  const item = makeReviewItem({
    kind: "provisioning_failed",
    payload: {
      project_id: "p",
      mode: "create",
      profile: "acme-site",
      error_code: "no_runner",
      error: null,
      attempt: 0,
    },
  });
  expect(slot.summary(item)).toBe(
    "Provisioning failed: no_runner. Accept retries it.",
  );
});
