// The project's Activity view (P2-17, FR-2.6). APP-01 (application test): the view
// always showed "The activity could not be loaded." because its first page param made the
// generated query throw before any request went out. Every fixture is synthetic.
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ActivityView } from "./ActivityView";

test("[P2-17][FR-2.6] APP-01 the Activity view asks for its first page and shows the empty state", async () => {
  const project = makeProject({ name: "Acme site" });
  const asked: URL[] = [];
  server.use(
    http.get(`/v1/projects/${project.id}/activity`, ({ request }) => {
      asked.push(new URL(request.url));
      return HttpResponse.json({ items: [], next_cursor: null });
    }),
  );

  renderWithProviders(<ActivityView projectId={project.id} />);

  expect(
    await screen.findByText("Nothing has happened in this project yet."),
  ).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(asked).toHaveLength(1);
  expect(asked[0]?.searchParams.get("cursor")).toBeNull();
  expect(asked[0]?.searchParams.get("limit")).toBe("50");
});
