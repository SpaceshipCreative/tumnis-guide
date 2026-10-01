// Detour capture in the focus bar at Guardrail (P4-01, FR-10.6): "Switched" opens the
// picker, the capture posts the title and the picked project with the answer, and the
// server's return question is answered with Return (the detour task's version read fresh,
// naming the question answered); a failed answer asks the question again.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { FocusCurrentOut } from "../../api/types.gen";
import { makeProblem, makeProject, makeTask } from "../../test/factories";
import { projectsList } from "../../test/msw/dashboard";
import {
  focusCurrent,
  focusMessage,
  focusReplies,
  focusSession,
  quietFocus,
} from "../../test/msw/focus";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

const BANK = "Call the bank about the card";

test("[P4-01][FR-10.6] switched at guardrail captures a detour and answers return", async () => {
  const admin = makeProject({ name: "Admin", archived_at: null });
  const session = focusSession("Write Acme invoice", 5);
  const checkIn = focusMessage({
    task_id: session.task_id,
    level: "guardrail",
    rule: "Guardrail · check_in_due (25 min cadence)",
  });
  const detourTask = makeTask({
    title: BANK,
    project_id: admin.id,
    version: 4,
  });
  const guardrail: FocusCurrentOut = {
    ...quietFocus(),
    level: "guardrail",
    workspace_level: "guardrail",
    session,
    messages: [checkIn],
  };
  const questionId = crypto.randomUUID();
  const asked: FocusCurrentOut = {
    ...guardrail,
    messages: [{ ...checkIn, response: "switched" }],
    detour: {
      event_id: questionId,
      detour_task_id: detourTask.id,
      detour_title: BANK,
      return_to_task_id: session.task_id,
      return_to_title: session.title,
      message: `Captured. Back to ${session.title}?`,
      rule: "Guardrail · detour",
    },
  };
  const replies = focusReplies(asked);
  const returns: unknown[] = [];
  server.use(
    projectsList([admin]),
    focusCurrent(guardrail),
    ...replies.handlers,
    http.get("*/v1/tasks/:taskId", () => HttpResponse.json(detourTask)),
    http.post("*/v1/focus/return", async ({ request }) => {
      returns.push(await request.json());
      return HttpResponse.json({ ...guardrail, messages: asked.messages });
    }),
  );
  const view = await renderRoute("/review", { viewport: "phone" });
  const bar = await screen.findByRole("region", { name: "Focus" });

  await view.user.click(within(bar).getByRole("button", { name: "Switched" }));
  const picker = within(bar).getByRole("form", { name: "Switched to" });
  await view.user.type(
    within(picker).getByLabelText("What are you switching to?"),
    BANK,
  );
  await waitFor(() => {
    expect(within(picker).getByLabelText("Project")).toHaveValue(admin.id);
  });
  await view.user.click(
    within(picker).getByRole("button", { name: "Capture" }),
  );

  await waitFor(() => {
    expect(replies.calls).toEqual([
      {
        path: "/focus/respond",
        body: {
          event_id: checkIn.id,
          response: "switched",
          detour: { title: BANK, project_id: admin.id },
        },
      },
    ]);
  });
  const question = await within(bar).findByRole("group", {
    name: "Back to your task?",
  });
  expect(
    within(question).getByText("Captured. Back to Write Acme invoice?"),
  ).toBeInTheDocument();
  expect(within(question).getByText("Guardrail · detour")).toBeInTheDocument();

  await view.user.click(
    within(question).getByRole("button", { name: "Return" }),
  );
  await waitFor(() => {
    expect(returns).toEqual([
      { decision: "return", version: 4, event_id: questionId },
    ]);
  });
  await waitFor(() => {
    expect(
      within(bar).queryByRole("group", { name: "Back to your task?" }),
    ).toBeNull();
  });
});

test("[P4-01][FR-10.6] a failed return answer asks the question again", async () => {
  const session = focusSession("Write Acme invoice", 5);
  const detourTask = makeTask({ title: BANK, version: 2 });
  const guardrail: FocusCurrentOut = {
    ...quietFocus(),
    level: "guardrail",
    workspace_level: "guardrail",
    session,
    messages: [],
  };
  const asked: FocusCurrentOut = {
    ...guardrail,
    detour: {
      event_id: crypto.randomUUID(),
      detour_task_id: detourTask.id,
      detour_title: BANK,
      return_to_task_id: session.task_id,
      return_to_title: session.title,
      message: `Captured. Back to ${session.title}?`,
      rule: "Guardrail · detour",
    },
  };
  let answers = 0;
  server.use(
    focusCurrent(asked),
    http.get("*/v1/tasks/:taskId", () => HttpResponse.json(detourTask)),
    http.post("*/v1/focus/return", () => {
      answers += 1;
      if (answers === 1) {
        return HttpResponse.json(
          makeProblem({ status: 409, code: "stale_version", title: "stale" }),
          {
            status: 409,
            headers: { "Content-Type": "application/problem+json" },
          },
        );
      }
      return HttpResponse.json(guardrail);
    }),
  );
  const view = await renderRoute("/review", { viewport: "phone" });
  const bar = await screen.findByRole("region", { name: "Focus" });

  const question = await within(bar).findByRole("group", {
    name: "Back to your task?",
  });
  await view.user.click(within(question).getByRole("button", { name: "Stay" }));

  expect(
    await within(bar).findByText("Your answer was not saved. Try again."),
  ).toBeInTheDocument();
  const again = await within(bar).findByRole("group", {
    name: "Back to your task?",
  });
  await view.user.click(within(again).getByRole("button", { name: "Stay" }));
  await waitFor(() => {
    expect(answers).toBe(2);
  });
});
