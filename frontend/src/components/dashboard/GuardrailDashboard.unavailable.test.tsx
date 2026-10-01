// The Guardrail dashboard when today's plan cannot be read (P4-01, FR-10.6; PR #142
// review): the card says so, as the other dashboard panels do, instead of offering a
// re-plan of a plan that may exist.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProblem } from "../../test/factories";
import { focusCurrent, quietFocus } from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P4-01][FR-10.6] a failed plan read says unavailable, not nothing planned", async () => {
  server.use(
    focusCurrent({
      ...quietFocus(),
      level: "guardrail",
      workspace_level: "guardrail",
    }),
    http.get("*/v1/plan/:day", () =>
      HttpResponse.json(
        makeProblem({ status: 500, code: "internal", title: "internal" }),
        {
          status: 500,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    ),
  );
  await renderRoute("/", { viewport: "laptop" });

  const now = await screen.findByRole("region", { name: "Now" });
  expect(
    await within(now).findByText("Today's plan could not be loaded."),
  ).toBeInTheDocument();
  expect(within(now).queryByText(/Nothing planned/)).toBeNull();
});
