// Optimistic updates (P0-22, REL-2): the edit shows at once; a 409 replaces it with the
// server's record (not the old local one) and says so.
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { ConflictToast } from "../components/common/ConflictToast";
import { makeTask } from "../test/factories";
import { server } from "../test/msw/server";
import { createTestQueryClient, renderWithProviders } from "../test/render";
import { taskQueryOptions, useUpdateTask } from "./optimistic";

function TaskTitle({ id }: { id: string }) {
  const { data } = useQuery(taskQueryOptions(id));
  const update = useUpdateTask();
  return (
    <main>
      <h1>{data?.title ?? "loading"}</h1>
      <button
        type="button"
        onClick={() => {
          if (data) {
            update.mutate({
              id,
              patch: { title: "B" },
              version: data.version,
            });
          }
        }}
      >
        Rename
      </button>
      <ConflictToast />
    </main>
  );
}

test("[P0-22][REL-2] T-P0-22-08 optimistic update rolls back on 409 and shows the server record", async () => {
  const task = makeTask({ title: "A", version: 3 });
  const current = { ...task, title: "C", version: 4 };
  // The 409 waits until the test has seen the optimistic "B", so that window is always
  // observable however slow the machine is (issue #76), instead of racing a real 50 ms.
  let answer409!: () => void;
  const conflictGate = new Promise<void>((resolve) => {
    answer409 = resolve;
  });
  server.use(
    http.patch("/v1/tasks/:id", async () => {
      await conflictGate;
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Stale version",
          status: 409,
          code: "stale_version",
          current,
        },
        {
          status: 409,
          headers: { "Content-Type": "application/problem+json" },
        },
      );
    }),
    http.get("/v1/tasks/:id", () => HttpResponse.json(current)),
  );
  const queryClient = createTestQueryClient();
  queryClient.setQueryData(taskQueryOptions(task.id).queryKey, task);
  const { user } = renderWithProviders(<TaskTitle id={task.id} />, {
    queryClient,
  });
  expect(screen.getByRole("heading")).toHaveTextContent("A");

  await act(() => user.click(screen.getByRole("button", { name: "Rename" })));

  expect(await screen.findByRole("heading", { name: "B" })).toBeVisible();
  answer409();
  await waitFor(() => {
    expect(screen.getByRole("heading")).toHaveTextContent("C");
  });
  expect(await screen.findByRole("status")).toHaveTextContent(
    /changed elsewhere/i,
  );
  expect(
    queryClient.getQueryData(taskQueryOptions(task.id).queryKey),
  ).toMatchObject({ title: "C", version: 4 });
});
