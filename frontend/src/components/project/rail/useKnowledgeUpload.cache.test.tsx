// APP-07 follow-up (P1-17, FR-15.2; CodeRabbit on #163): an upload the server accepted
// while the knowledge list isn't cached (the phone's Context sheet is closed) and the list
// GET fails still shows: the hook seeds the list with the accepted document and marks it
// stale, so the Knowledge section fetches the whole list when it shows.
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";

import { knowledgeListDocumentsOptions } from "../../../api/@tanstack/react-query.gen";
import { makeProblem } from "../../../test/factories";
import { KnowledgeFake } from "../../../test/msw/knowledge";
import { server } from "../../../test/msw/server";
import { createTestQueryClient } from "../../../test/render";
import { knowledgeListArgs, useKnowledgeUpload } from "./useKnowledgeUpload";

test("[P1-17][FR-15.2] an accepted upload shows in the list when the list GET fails", async () => {
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

  // The list was never loaded: the accepted document is cached on its own, marked stale
  // so the whole list is fetched again when the Knowledge section shows it.
  const key = knowledgeListDocumentsOptions(
    knowledgeListArgs(projectId),
  ).queryKey;
  expect(client.getQueryData(key)?.items.map((d) => d.id)).toEqual([doc.id]);
  expect(client.getQueryState(key)?.isInvalidated).toBe(true);
});
