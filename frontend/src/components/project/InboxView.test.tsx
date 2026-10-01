// The project's Inbox view (P2-17, FR-2.6): proposals from this project's email, chat and
// notes wait here for a decision. Phase 3 (P3-07) fills it; until then it says so. Every
// fixture is synthetic.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { zReviewItemOut } from "../../api/zod.gen";
import { factoryFor, makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";

interface InboxViewModule {
  InboxView: (props: { projectId: string }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before InboxView.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

const makeItem = factoryFor(zReviewItemOut);

test.fails(
  "[P2-17][FR-2.6] T-P2-17-09 empty state until proposals exist",
  async () => {
    const { InboxView } = await load<InboxViewModule>("./InboxView");
    const project = makeProject({ name: "Acme site" });
    let items: unknown[] = [];
    server.use(
      http.get(`/v1/projects/${project.id}/inbox`, () =>
        HttpResponse.json({ items, next_cursor: null }),
      ),
    );

    const first = renderWithProviders(<InboxView projectId={project.id} />);
    expect(await screen.findByText("No proposals yet")).toBeVisible();
    expect(
      screen.getByText(
        "Tasks the agent suggests from this project's email, chat and notes wait here.",
      ),
    ).toBeVisible();
    first.unmount();

    items = [
      makeItem({
        kind: "proposal",
        project_id: project.id,
        target_type: "task",
        target_title: "Update pricing page copy",
        payload: { title: "Update pricing page copy", source: "note" },
        actions: ["accept", "edit", "reject", "snooze"],
        primary_action: "accept",
      }),
    ];
    renderWithProviders(<InboxView projectId={project.id} />);
    const list = await screen.findByRole("list", { name: "Inbox" });
    expect(within(list).getByText("Update pricing page copy")).toBeVisible();
    expect(screen.queryByText("No proposals yet")).toBeNull();
  },
);
