// APP-F07 (final application test): the Search page showed a snippet's match markers as
// text ("Open the <b>footer</b> component") and gave no sign where the snippet was cut.
// `GET /v1/search` marks each match with `<b>` and `</b>` (ts_headline's defaults) and puts
// "…" where text was left out; the page shows the matches highlighted, never the tags.
// Every fixture is synthetic.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { SearchHit } from "../api/types.gen";
import { server } from "../test/msw/server";
import { renderRoute } from "../test/render";

const PROJECT_ID = "0192b3c4-0000-7000-8000-0000000000a1";

function hit(n: number, title: string, snippet: string): SearchHit {
  return {
    entity_id: `0192b3c4-0000-7000-8000-0000000000c${String(n)}`,
    entity_type: "task",
    project_id: PROJECT_ID,
    score: 1 / n,
    snippet,
    title,
  };
}

test("[P0-24][FR-3.9] APP-F07 a snippet shows its matches highlighted and its cuts as …", async () => {
  server.use(
    http.get("*/v1/search", () =>
      HttpResponse.json({
        items: [
          hit(
            1,
            "Write Acme proposal",
            "…Outline the three <b>proposal</b> sections and then send them…",
          ),
          hit(2, "Fix footer link", "…<b>footer</b> link"),
        ],
        next_cursor: null,
      }),
    ),
  );
  await renderRoute("/search?q=proposal", { viewport: "laptop" });

  const results = await screen.findByRole("list", { name: "Results" });
  const proposal = within(results).getByRole("link", {
    name: /Write Acme proposal/,
  });
  expect(proposal).not.toHaveTextContent("<b>");
  expect(proposal).toHaveTextContent(
    "…Outline the three proposal sections and then send them…",
  );
  const marked = within(proposal).getByText("proposal", { selector: "mark" });
  expect(marked).toBeVisible();

  // A snippet that is only a piece of the title adds nothing under it.
  const footer = within(results).getByRole("link", {
    name: /Fix footer link/,
  });
  expect(footer).toHaveTextContent(/^Fix footer linkTask$/);
});
