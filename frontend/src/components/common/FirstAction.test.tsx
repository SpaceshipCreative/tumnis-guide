// The first-action line (P1-08, UX 5, FR-4.6): the Generation slot's placeholder shows
// first with its marker, a task still waiting shows `First action pending`, and the
// project agent's value replaces both once the `/ws` message refetches the task.
import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { connectLive } from "../../lib/ws";
import { FakeSocket } from "../../test/fakeSocket";
import { makeTask } from "../../test/factories";
import { server } from "../../test/msw/server";
import { createTestQueryClient, renderWithProviders } from "../../test/render";
import { TaskFirstAction } from "./FirstAction";

const PLACEHOLDER = "Open the invoice template";
const AGENT = "Open last month's invoice in Wave and duplicate it";

type Json = Record<string, unknown>;

function taskJson(overrides: Json): Json {
  return { ...makeTask(), ...overrides };
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test.fails(
  "[P1-08][UX 5] T-P1-08-15 placeholder then agent value",
  async () => {
    const placeholder = taskJson({
      title: "Send Acme the March invoice",
      first_action: PLACEHOLDER,
      first_action_source: "placeholder",
      enrichment_status: "running",
      version: 2,
    });
    const pending = taskJson({
      title: "Book the venue",
      first_action: null,
      first_action_source: null,
      enrichment_status: "running",
      version: 1,
    });
    const current: Record<string, Json> = {
      [String(placeholder.id)]: placeholder,
      [String(pending.id)]: pending,
    };
    server.use(
      http.get("/v1/tasks/:id", ({ params }) =>
        HttpResponse.json(current[String(params.id)]),
      ),
    );
    const queryClient = createTestQueryClient();
    const stop = connectLive(queryClient, "ws://localhost/ws");
    FakeSocket.latest().open();
    renderWithProviders(
      <>
        <TaskFirstAction taskId={String(placeholder.id)} />
        <TaskFirstAction taskId={String(pending.id)} />
      </>,
      { queryClient },
    );

    await waitFor(() => {
      expect(screen.getAllByTestId("first-action")).toHaveLength(2);
    });
    const [first, second] = screen.getAllByTestId("first-action");
    expect(first).toHaveAttribute("data-state", "placeholder");
    expect(first).toHaveTextContent(PLACEHOLDER);
    expect(second).toHaveAttribute("data-state", "pending");
    expect(second).toHaveTextContent("First action pending");

    for (const task of [placeholder, pending]) {
      current[String(task.id)] = {
        ...task,
        first_action: AGENT,
        first_action_source: "agent",
        enrichment_status: "done",
        version: Number(task.version) + 1,
      };
    }
    act(() => {
      FakeSocket.latest().receive({
        entity: "task",
        id: String(placeholder.id),
      });
      FakeSocket.latest().receive({ entity: "task", id: String(pending.id) });
    });

    await waitFor(() => {
      for (const line of screen.getAllByTestId("first-action")) {
        expect(line).toHaveTextContent(AGENT);
        expect(line).toHaveAttribute("data-state", "agent");
      }
    });
    expect(screen.queryByText("First action pending")).toBeNull();
    stop();
  },
);
