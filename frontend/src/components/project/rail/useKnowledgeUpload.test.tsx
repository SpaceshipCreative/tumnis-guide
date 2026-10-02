// A1.5 / APP-07 (P1-17, FR-15.2): once the server has accepted a file (202), the upload
// has succeeded. A failing knowledge-list GET must not turn it into a reported failure,
// because a retry would send a new idempotency key and could store the file twice.
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";

import { makeProblem } from "../../../test/factories";
import { KnowledgeFake } from "../../../test/msw/knowledge";
import { server } from "../../../test/msw/server";
import { createTestQueryClient } from "../../../test/render";
import { useKnowledgeUpload } from "./useKnowledgeUpload";

test("[P1-17][FR-15.2] an accepted upload stays a success when the list GET fails", async () => {
  const projectId = crypto.randomUUID();
  const knowledge = new KnowledgeFake({ projectId });
  server.resetHandlers();
  server.use(
    http.get("/v1/knowledge/documents", () =>
      HttpResponse.json(makeProblem({ status: 500 }), { status: 500 }),
    ),
    ...knowledge.handlers,
  );
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useKnowledgeUpload(projectId), {
    wrapper,
  });

  const doc = await result.current(
    new File(["%PDF-1.7 rates"], "rate-card-table.pdf", {
      type: "application/pdf",
    }),
  );

  expect(doc.title).toBe("rate-card-table.pdf");
  expect(doc.status).toBe("pending_scan");
  expect(knowledge.uploads).toEqual([
    { project_id: projectId, filename: "rate-card-table.pdf" },
  ]);
});
