// T-P1-17-16 (FR-15.4): the task drawer's packet preview shows what an enrichment run
// would get: the brief first, then each passage cited by document and page.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { PacketPreview } from "./PacketPreview";

const TASK = "0192f3a4-5555-7000-8000-000000000005";
const DOC = "0192f3a4-6666-7000-8000-000000000006";

test.fails(
  "[P1-17][FR-15.4] preview shows brief then passages with pages",
  async () => {
    const asked: string[] = [];
    server.resetHandlers();
    server.use(
      http.get(`/v1/tasks/${TASK}/packet`, ({ request }) => {
        asked.push(new URL(request.url).search);
        return HttpResponse.json({
          schema_version: 1,
          run_id: "0192f3a4-7777-7000-8000-000000000007",
          correlation_id: "0192f3a4-8888-7000-8000-000000000008",
          kind: "enrich",
          profile_id: "0192f3a4-9999-7000-8000-000000000009",
          skill: "enrich_task",
          output_schema: { name: "enrichment_result", version: 1 },
          prompt_text: "Fill in the missing fields.",
          body: {
            brief: "Logo refresh for Acme. Keep the wordmark.",
            passages: [
              {
                document_id: DOC,
                title: "Rate card",
                heading_path: ["Rates", "Design"],
                page: 2,
                text: "Senior designer: 650 a day.",
              },
            ],
            estimate_history: [],
          },
        });
      }),
    );

    renderWithProviders(<PacketPreview taskId={TASK} />);

    const preview = await screen.findByRole("region", {
      name: "Packet preview",
    });
    const brief = await within(preview).findByText(
      "Logo refresh for Acme. Keep the wordmark.",
    );
    const cite = within(preview).getByText("Rate card, page 2");
    expect(
      within(preview).getByText("Senior designer: 650 a day."),
    ).toBeVisible();
    // The brief comes before the first passage.
    expect(
      brief.compareDocumentPosition(cite) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(asked).toEqual(["?kind=enrich"]);
  },
);
