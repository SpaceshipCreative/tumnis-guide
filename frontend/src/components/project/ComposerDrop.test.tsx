// A1.5 / APP-07 (P1-17, FR-15.2): the composer is the project's drop target for files. A
// file dropped on it is uploaded to the project's knowledge, and the Knowledge section
// opens (the rail on a laptop, the Context sheet on a phone) with the new item showing
// "Scanning" until the extract worker settles it.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { KnowledgeFake } from "../../test/msw/knowledge";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

function dropOn(target: HTMLElement, file: File): void {
  const dataTransfer = { files: [file], types: ["Files"], dropEffect: "none" };
  for (const type of ["dragEnter", "dragOver", "drop"] as const) {
    fireEvent[type](target, { dataTransfer });
  }
}

test("[P1-17][FR-15.2] a file dropped on the composer is uploaded and shows in Knowledge", async () => {
  for (const viewport of ["laptop", "phone"] as const) {
    const project = makeProject({ name: "Acme site" });
    const knowledge = new KnowledgeFake({ projectId: project.id });
    const fake = new ProjectFake({ project, tasks: [] });
    server.resetHandlers();
    server.use(...knowledge.handlers, ...fake.handlers);

    const { unmount } = await renderRoute(
      `/projects/${project.id}?view=tasks`,
      { viewport },
    );
    const composer = await screen.findByRole("form", { name: "Composer" });
    expect(screen.queryByRole("region", { name: "Knowledge" })).toSatisfy(
      (region: HTMLElement | null) =>
        region === null ||
        within(region).queryAllByRole("listitem").length === 0,
    );

    dropOn(
      composer,
      new File(["%PDF-1.7 rates"], "rate-card-table.pdf", {
        type: "application/pdf",
      }),
    );

    const rail = await screen.findByRole("region", { name: "Knowledge" });
    const item = await within(rail).findByRole("listitem");
    expect(item).toHaveTextContent("rate-card-table.pdf");
    expect(item).toHaveTextContent("Scanning");
    expect(knowledge.uploads).toEqual([
      { project_id: project.id, filename: "rate-card-table.pdf" },
    ]);
    if (viewport === "phone") {
      expect(screen.getByRole("dialog", { name: "Context" })).toContainElement(
        rail,
      );
    }
    unmount();
  }
});

test("[P1-17][FR-15.2] a dropped file's item turns Ready once extraction settles", async () => {
  const project = makeProject({ name: "Acme site" });
  const knowledge = new KnowledgeFake({ projectId: project.id });
  const fake = new ProjectFake({ project, tasks: [] });
  server.resetHandlers();
  server.use(...knowledge.handlers, ...fake.handlers);

  await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  dropOn(
    await screen.findByRole("form", { name: "Composer" }),
    new File(["%PDF-1.7 rates"], "rate-card-table.pdf", {
      type: "application/pdf",
    }),
  );
  const rail = await screen.findByRole("region", { name: "Knowledge" });
  const item = await within(rail).findByRole("listitem");
  expect(item).toHaveTextContent("Scanning");

  // The worker finishes (the server names the file by its type); the list, polling
  // while an item settles, shows it Ready.
  for (const doc of knowledge.documents.values()) {
    knowledge.documents.set(doc.id, { ...doc, kind: "pdf", status: "ready" });
  }
  await waitFor(
    () => {
      expect(within(rail).getByRole("listitem")).toHaveTextContent("Ready");
    },
    { timeout: 3_000 },
  );
});
