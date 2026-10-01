// Run from the task drawer (P2-04, FR-5.4, FR-5.5): an AI or Hybrid task's drawer has a
// Run button that asks for a run (`POST /v1/tasks/{id}/run`, 202) and puts the run in
// the URL (`run`); with `run` set the drawer shows that run's view, still titled with
// the task, and Back returns to the task. A Human task has no Run button.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderRoute } from "../../../test/render";

const RUN_ID = "01950000-0000-7000-8000-000000000a01";

function runHandlers(recorder: Recorder, taskId: string, projectId: string) {
  return [
    http.post("*/v1/tasks/:id/run", async ({ request }) => {
      await recorder.record(request);
      return HttpResponse.json(
        { run_id: RUN_ID, status: "queued" },
        { status: 202 },
      );
    }),
    http.get("*/v1/runs/:id", () =>
      HttpResponse.json({
        id: RUN_ID,
        task_id: taskId,
        project_id: projectId,
        kind: "task",
        status: "running",
        stop_reason: null,
        rerun_of: null,
        started_at: new Date().toISOString(),
        finished_at: null,
        created_at: new Date().toISOString(),
      }),
    ),
    http.get("*/v1/runs/:id/events", () =>
      HttpResponse.json({
        items: [
          {
            seq: 1,
            message_id: "01950000-0000-7000-8000-000000000b01",
            kind: "log",
            payload: { kind: "log", text: "Reading the footer component" },
            at: new Date().toISOString(),
          },
        ],
        next_after_seq: 1,
      }),
    ),
  ];
}

test("[P2-04][FR-5.4] Run in the drawer asks for a run and shows it", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({
      project_id: project.id,
      title: "Fix footer link",
      label: "ai",
      status: "today",
    });
    const fake = new ProjectFake({ project, tasks: [task] });
    const recorder = new Recorder();
    server.resetHandlers();
    server.use(...fake.handlers, ...runHandlers(recorder, task.id, project.id));

    const { user, unmount, router } = await renderRoute(
      `/projects/${project.id}?view=tasks&task=${task.id}`,
      { viewport },
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Fix footer link",
    });
    await user.click(within(drawer).getByRole("button", { name: "Run" }));

    // One request for a run, sent once with an Idempotency-Key.
    await waitFor(() => {
      expect(recorder.writes()).toEqual([`POST /v1/tasks/${task.id}/run`]);
    });
    expect(recorder.sent[0]?.idempotencyKey).toBeTruthy();

    // The run goes into the URL after the task, and the drawer shows it.
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({
        task: task.id,
        run: RUN_ID,
      });
    });
    expect(router.state.location.href).toMatch(
      new RegExp(`[?&]task=${task.id}(&.*)?[&]run=${RUN_ID}`),
    );
    const open = screen.getByRole("dialog", { name: "Fix footer link" });
    const log = await within(open).findByRole("log", { name: "Run log" });
    expect(
      await within(log).findByText("Reading the footer component"),
    ).toBeVisible();
    expect(within(open).getByRole("button", { name: "Stop" })).toBeVisible();

    // Back to the task: the run leaves the URL, the details come back.
    await user.click(
      within(open).getByRole("button", { name: "Back to the task" }),
    );
    await waitFor(() => {
      expect(router.state.location.search).not.toHaveProperty("run");
    });
    expect(
      await within(open).findByRole("textbox", { name: "Title" }),
    ).toHaveValue("Fix footer link");
    unmount();
  }
});

test("[P2-04][FR-5.4] a Human task has no Run button", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Call the printer",
    label: "human",
    status: "today",
  });
  const runnable = makeTask({
    project_id: project.id,
    title: "Fix footer link",
    label: "hybrid",
    status: "backlog",
  });
  const fake = new ProjectFake({ project, tasks: [task, runnable] });
  server.resetHandlers();
  server.use(...fake.handlers);

  const human = await renderRoute(`/projects/${project.id}?task=${task.id}`, {
    viewport: "laptop",
  });
  const drawer = await screen.findByRole("dialog", {
    name: "Call the printer",
  });
  expect(within(drawer).queryByRole("button", { name: "Run" })).toBeNull();
  human.unmount();

  const hybrid = await renderRoute(
    `/projects/${project.id}?task=${runnable.id}`,
    { viewport: "laptop" },
  );
  const open = await screen.findByRole("dialog", { name: "Fix footer link" });
  expect(within(open).getByRole("button", { name: "Run" })).toBeVisible();
  hybrid.unmount();
});

test.fails(
  "[P2-04][FR-5.4] a Run answer that lands after another task opens is ignored",
  async () => {
    // CodeRabbit on #120: the answer to task A's Run arrives after the user has opened
    // task B; B's drawer stays on B's details and the URL gets no `run`.
    const project = makeProject({ name: "Acme site" });
    const first = makeTask({
      project_id: project.id,
      title: "Fix footer link",
      label: "ai",
      status: "today",
    });
    const second = makeTask({
      project_id: project.id,
      title: "Write the release notes",
      label: "ai",
      status: "today",
    });
    const fake = new ProjectFake({ project, tasks: [first, second] });
    const recorder = new Recorder();
    let answer: () => void = () => undefined;
    const answered = new Promise<void>((resolve) => {
      answer = resolve;
    });
    server.resetHandlers();
    server.use(
      ...fake.handlers,
      http.post("*/v1/tasks/:id/run", async ({ request }) => {
        await recorder.record(request);
        await answered;
        return HttpResponse.json(
          { run_id: RUN_ID, status: "queued" },
          { status: 202 },
        );
      }),
      ...runHandlers(recorder, first.id, project.id),
    );

    const { user, unmount, router, queryClient } = await renderRoute(
      `/projects/${project.id}?view=tasks&task=${first.id}`,
      { viewport: "laptop" },
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Fix footer link",
    });
    await user.click(within(drawer).getByRole("button", { name: "Run" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([`POST /v1/tasks/${first.id}/run`]);
    });

    // Task B opens from the list while A's Run is still waiting for its answer.
    await user.click(
      screen.getByRole("button", { name: "Write the release notes" }),
    );
    const next = await screen.findByRole("dialog", {
      name: "Write the release notes",
    });
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ task: second.id });
    });

    answer();
    await waitFor(() => {
      expect(queryClient.isMutating()).toBe(0);
      expect(router.state.isLoading).toBe(false);
    });

    expect(router.state.location.search).toMatchObject({ task: second.id });
    expect(router.state.location.search).not.toHaveProperty("run");
    expect(within(next).queryByRole("log", { name: "Run log" })).toBeNull();
    expect(within(next).getByRole("textbox", { name: "Title" })).toHaveValue(
      "Write the release notes",
    );
    unmount();
  },
);
