// Harness self tests for the render helper and the factories (P0-02).
import { useQuery } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "./factories";
import { server } from "./msw/server";
import { createTestQueryClient, renderWithProviders } from "./render";

function ProjectName() {
  const { data } = useQuery({
    queryKey: ["project"],
    queryFn: async () => {
      const response = await fetch("http://localhost/v1/projects/p1");
      return (await response.json()) as { name: string };
    },
  });
  return <h1>{data?.name ?? "loading"}</h1>;
}

test("[P0-02][Quality-rule-5] renderWithProviders serves queries from a per-test MSW handler", async () => {
  server.use(
    http.get("http://localhost/v1/projects/p1", () =>
      HttpResponse.json(makeProject({ name: "Acme rebrand" })),
    ),
  );
  const queryClient = createTestQueryClient();

  renderWithProviders(<ProjectName />, {
    route: "/projects/p1",
    queryClient,
  });

  expect(
    await screen.findByRole("heading", { name: "Acme rebrand" }),
  ).toBeInTheDocument();
  expect(window.location.pathname).toBe("/projects/p1");
  expect(queryClient.getQueryData(["project"])).toMatchObject({
    name: "Acme rebrand",
  });
});

test("[P0-02][Quality-rule-5] factories give unique ids and accept overrides", () => {
  const project = makeProject();
  const first = makeTask({ project_id: project.id, title: "Send drafts" });
  const second = makeTask({ project_id: project.id });

  expect(first.id).not.toBe(second.id);
  expect(first.title).toBe("Send drafts");
  expect(second.project_id).toBe(project.id);
  expect(first.status).toBe("backlog");
});
