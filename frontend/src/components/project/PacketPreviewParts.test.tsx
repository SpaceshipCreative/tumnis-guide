// A1.5 (P1-17, FR-15.4): the packet preview's parts are articles, the brief first and then
// one per passage, so each part reads (and is found) on its own.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { PacketPreview } from "./PacketPreview";

const TASK = "0192f3a4-5555-7000-8000-00000000000a";

test("[P1-17][FR-15.4] the brief and each passage are articles, brief first", async () => {
  server.resetHandlers();
  server.use(
    http.get(`/v1/tasks/${TASK}/packet`, () =>
      HttpResponse.json({
        kind: "enrich",
        schema_version: 1,
        body: {
          brief: "Quote from the signed rate card.",
          passages: [
            {
              document_id: "0192f3a4-6666-7000-8000-00000000000b",
              title: "Rate card",
              heading_path: ["Rates"],
              page: 2,
              text: "Senior designer | 160",
            },
            {
              document_id: "0192f3a4-6666-7000-8000-00000000000c",
              title: "Terms",
              heading_path: [],
              page: null,
              text: "Invoices are due within thirty days.",
            },
          ],
        },
      }),
    ),
  );

  renderWithProviders(<PacketPreview taskId={TASK} />);

  const preview = await screen.findByRole("region", { name: "Packet preview" });
  const parts = await within(preview).findAllByRole("article");
  expect(parts.map((p) => p.textContent)).toEqual([
    "BriefQuote from the signed rate card.",
    "Rate card, page 2Senior designer | 160",
    "TermsInvoices are due within thirty days.",
  ]);
});
