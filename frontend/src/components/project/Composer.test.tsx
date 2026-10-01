// The composer (P0-24, FR-2.5): one input; Enter creates a task in this project (label
// pending, Backlog), shown at once under Up next and kept once the server answers.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-24][FR-2.5] T-P0-24-14 Enter creates a task in this project", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const fake = new ProjectFake({ project });
    let release: () => void = () => undefined;
    fake.createGate = new Promise((resolve) => {
      release = resolve;
    });
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?view=tasks`,
      { viewport },
    );
    const input = await screen.findByRole("textbox", { name: "New task" });
    await user.type(input, "Send logo drafts{Enter}");

    // Optimistic: the row shows before the server answers, and the input is cleared.
    const upNext = screen.getByRole("region", { name: "Up next" });
    expect(await within(upNext).findByText("Send logo drafts")).toBeVisible();
    expect(input).toHaveValue("");
    expect(fake.sent("POST", "/v1/tasks").map((s) => s.body)).toEqual([
      { project_id: project.id, title: "Send logo drafts" },
    ]);
    expect(fake.sent("POST", "/v1/tasks")[0]?.idempotencyKey).toBeTruthy();

    release();
    await waitFor(() => {
      expect(fake.tasks.size).toBe(1);
    });
    await waitFor(() => {
      expect(within(upNext).getAllByText("Send logo drafts")).toHaveLength(1);
    });
    unmount();
  }
});

// Ask the agent (P2-17, FR-2.5): the composer's toggle turns the same input into a
// question. Enter sends it to POST /v1/projects/{id}/ask (one Idempotency-Key), which
// makes an AI task, so the question lands in the task list and nowhere else: no chat
// panel opens. The toggle goes back to task mode after sending.
test.fails("[P2-17][FR-2.5] T-P2-17-07 ask toggle posts to ask", async () => {
  const question = "Which typefaces does the brand guide name?";
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const fake = new ProjectFake({ project });
    const asked: { body: unknown; key: string | null }[] = [];
    server.resetHandlers();
    server.use(
      ...fake.handlers,
      http.post(`/v1/projects/${project.id}/ask`, async ({ request }) => {
        const body = (await request.json()) as { question: string };
        asked.push({ body, key: request.headers.get("Idempotency-Key") });
        const task = makeTask({
          project_id: project.id,
          parent_id: null,
          title: body.question,
          label: "ai",
          source: "ask",
          status: "backlog",
          first_action: "Answer the question",
        });
        fake.tasks.set(task.id, task);
        return HttpResponse.json(
          { task, run_id: crypto.randomUUID() },
          { status: 201 },
        );
      }),
    );

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?view=tasks`,
      { viewport },
    );
    const toggle = await screen.findByRole("button", { name: "Ask the agent" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");

    const input = screen.getByRole("textbox", { name: "Ask the agent" });
    await user.type(input, `${question}{Enter}`);

    await waitFor(() => {
      expect(asked).toHaveLength(1);
    });
    expect(asked[0]?.body).toEqual({ question });
    expect(asked[0]?.key).toBeTruthy();
    expect(fake.sent("POST", "/v1/tasks")).toEqual([]);

    // Back in task mode, with the input cleared.
    await waitFor(() => {
      expect(toggle).toHaveAttribute("aria-pressed", "false");
    });
    expect(screen.getByRole("textbox", { name: "New task" })).toHaveValue("");
    // The question is a task in the list; no chat opens.
    expect(await screen.findByText(question)).toBeVisible();
    expect(screen.queryByRole("log")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    unmount();
  }
});
