// T-P1-17-17 (FR-15.6): the Knowledge rail section. Its one-line summary gives the item
// count and the quota; opened, it adds a text entry, an upload and a link, pins an item
// and moves one to the trash with Undo. The same section is in the phone Context sheet.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import {
  makeDocument,
  makeProblem,
  makeProject,
} from "../../../test/factories";
import { KnowledgeFake } from "../../../test/msw/knowledge";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

const GIB = 1024 ** 3;

function setup() {
  const project = makeProject({ name: "Acme site" });
  const rateCard = makeDocument({
    project_id: project.id,
    title: "Rate card",
    kind: "file",
    role: null,
    pinned: false,
    version: 3,
    status: "ready",
  });
  const notes = makeDocument({
    project_id: project.id,
    title: "Kickoff notes",
    kind: "text",
    role: null,
    pinned: false,
    version: 1,
    status: "ready",
  });
  const knowledge = new KnowledgeFake({
    projectId: project.id,
    documents: [rateCard, notes],
    quota: { used_bytes: 1536 * 1024, quota_bytes: 10 * GIB },
  });
  const fake = new ProjectFake({ project, tasks: [], brief: "Logo refresh" });
  server.resetHandlers();
  server.use(...knowledge.handlers, ...fake.handlers);
  return { project, rateCard, knowledge };
}

test("[P1-17][FR-15.6] count, quota, add, pin, trash", async () => {
  const { project, rateCard, knowledge } = setup();
  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  const rail = await screen.findByRole("complementary", { name: "Context" });

  // One line: how many items, and the space used against the workspace quota.
  const section = await within(rail).findByRole("button", {
    name: /^Knowledge/,
  });
  await waitFor(() => {
    expect(section).toHaveTextContent("2 items · 1.5 MB of 10 GB");
  });

  await user.click(section);
  expect(await within(rail).findByText("Rate card")).toBeVisible();
  expect(within(rail).getByText("Kickoff notes")).toBeVisible();

  // Add a text entry.
  await user.click(within(rail).getByRole("button", { name: "Add text" }));
  await user.type(within(rail).getByRole("textbox", { name: "Title" }), "Tone");
  await user.type(
    within(rail).getByRole("textbox", { name: "Text" }),
    "Calm and plain.",
  );
  await user.click(within(rail).getByRole("button", { name: "Add" }));
  expect(await within(rail).findByText("Tone")).toBeVisible();
  expect(knowledge.sentBodies("POST", "/v1/knowledge/documents/text")).toEqual([
    { project_id: project.id, title: "Tone", body_md: "Calm and plain." },
  ]);

  // Upload a file.
  await user.upload(
    within(rail).getByLabelText("Upload a file"),
    new File(["%PDF-1.7 brief"], "brief.pdf", { type: "application/pdf" }),
  );
  expect(await within(rail).findByText("brief.pdf")).toBeVisible();
  expect(knowledge.uploads).toEqual([
    { project_id: project.id, filename: "brief.pdf" },
  ]);

  // Add a link.
  await user.click(within(rail).getByRole("button", { name: "Add link" }));
  await user.type(
    within(rail).getByRole("textbox", { name: "Link" }),
    "https://example.com/style",
  );
  await user.click(within(rail).getByRole("button", { name: "Add" }));
  await waitFor(() => {
    expect(
      knowledge.sentBodies("POST", "/v1/knowledge/documents/link"),
    ).toEqual([{ project_id: project.id, url: "https://example.com/style" }]);
  });

  // Pin, with the version read.
  await user.click(within(rail).getByRole("button", { name: "Pin Rate card" }));
  expect(
    await within(rail).findByRole("button", { name: "Unpin Rate card" }),
  ).toBeVisible();
  expect(
    knowledge.sentBodies("PATCH", `/v1/knowledge/documents/${rateCard.id}`),
  ).toEqual([{ pinned: true, version: 3 }]);

  // Trash, then Undo brings it back.
  await user.click(
    within(rail).getByRole("button", { name: "Move Rate card to trash" }),
  );
  await waitFor(() => {
    expect(within(rail).queryByText("Rate card")).toBeNull();
  });
  await user.click(await screen.findByRole("button", { name: "Undo" }));
  expect(await within(rail).findByText("Rate card")).toBeVisible();
  expect(knowledge.writes()).toEqual(
    expect.arrayContaining([
      `DELETE /v1/knowledge/documents/${rateCard.id}`,
      `POST /v1/knowledge/documents/${rateCard.id}/restore`,
    ]),
  );
});

test("[P1-17][FR-15.6] the phone Context sheet has the Knowledge section", async () => {
  const { project } = setup();
  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "phone",
  });
  await user.click(await screen.findByRole("button", { name: "Context" }));
  const sheet = screen.getByRole("dialog", { name: "Context" });
  const section = await within(sheet).findByRole("button", {
    name: /^Knowledge/,
  });
  await waitFor(() => {
    expect(section).toHaveTextContent("2 items · 1.5 MB of 10 GB");
  });
  await user.click(section);
  expect(await within(sheet).findByText("Rate card")).toBeVisible();
});

test("[P1-17][FR-15.6] a failed trash keeps the item and offers no Undo (#134)", async () => {
  // CodeRabbit on #134: the item was marked trashed before the DELETE answered, so a
  // failed trash still said "moved to trash" with Undo beside the error.
  const { project, rateCard } = setup();
  server.use(
    http.delete(`/v1/knowledge/documents/${rateCard.id}`, () =>
      HttpResponse.json(
        makeProblem({ status: 500, code: "internal", title: "internal" }),
        {
          status: 500,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    ),
  );
  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  const rail = await screen.findByRole("complementary", { name: "Context" });
  await user.click(
    await within(rail).findByRole("button", { name: /^Knowledge/ }),
  );
  expect(await within(rail).findByText("Rate card")).toBeVisible();

  await user.click(
    within(rail).getByRole("button", { name: "Move Rate card to trash" }),
  );
  expect(
    await within(rail).findByText("Could not move Rate card to the trash."),
  ).toBeVisible();
  expect(within(rail).queryByText(/moved to trash/)).toBeNull();
  expect(within(rail).queryByRole("button", { name: "Undo" })).toBeNull();
  expect(within(rail).getByText("Rate card")).toBeVisible();
});
