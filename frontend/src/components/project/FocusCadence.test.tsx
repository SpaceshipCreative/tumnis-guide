// The project's focus check-in cadence in the Settings rail (P2-15, FR-10.1): one PATCH
// with `focus_cadence_min` and the version read; empty means the default (25).
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { FocusCadence } from "./FocusCadence";

function answering(
  recorder: Recorder,
  project: ReturnType<typeof makeProject>,
) {
  return http.patch("*/v1/projects/:projectId", async ({ request }) => {
    const sent = await recorder.record(request);
    return HttpResponse.json({
      ...project,
      ...(sent.body as Record<string, unknown>),
      version: project.version + 1,
    });
  });
}

test("[P2-15][FR-10.1] the cadence saves with one PATCH", async () => {
  const project = makeProject({ name: "Beta app", version: 4 });
  const recorder = new Recorder();
  server.use(answering(recorder, project));
  const { user } = renderWithProviders(<FocusCadence project={project} />, {
    viewport: "phone",
  });

  const field = screen.getByRole("spinbutton", {
    name: "Focus check-in cadence (minutes)",
  });
  expect(field).toHaveAttribute("placeholder", "25 (default)");
  await user.type(field, "40");
  await user.click(screen.getByRole("button", { name: "Save cadence" }));

  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  expect(recorder.sent[0]?.path).toBe(`/v1/projects/${project.id}`);
  expect(recorder.sent[0]?.body).toEqual({ focus_cadence_min: 40, version: 4 });
});

test("[P2-15][FR-10.1] an empty cadence goes back to the default", async () => {
  const project = { ...makeProject({ version: 2 }), focus_cadence_min: 50 };
  const recorder = new Recorder();
  server.use(answering(recorder, project));
  const { user } = renderWithProviders(<FocusCadence project={project} />, {
    viewport: "laptop",
  });

  const field = screen.getByRole("spinbutton", {
    name: "Focus check-in cadence (minutes)",
  });
  expect(field).toHaveValue(50);
  await user.clear(field);
  await user.click(screen.getByRole("button", { name: "Save cadence" }));

  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  expect(recorder.sent[0]?.body).toEqual({
    focus_cadence_min: null,
    version: 2,
  });
});

test("[P2-15][FR-10.1] a cadence out of range is refused before any PATCH", async () => {
  const project = makeProject({ version: 1 });
  const recorder = new Recorder();
  server.use(answering(recorder, project));
  const { user } = renderWithProviders(<FocusCadence project={project} />, {
    viewport: "phone",
  });

  const field = screen.getByRole("spinbutton", {
    name: "Focus check-in cadence (minutes)",
  });
  await user.type(field, "300");
  await user.click(screen.getByRole("button", { name: "Save cadence" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Use whole minutes from 5 to 240",
  );
  expect(field).toHaveAttribute("aria-invalid", "true");
  expect(recorder.sent).toEqual([]);
});

test("[P2-15][FR-10.1] text that is not a number is refused, not saved as the default", async () => {
  const project = { ...makeProject({ version: 3 }), focus_cadence_min: 30 };
  const recorder = new Recorder();
  server.use(answering(recorder, project));
  const { user } = renderWithProviders(<FocusCadence project={project} />, {
    viewport: "phone",
  });

  const field = screen.getByRole("spinbutton", {
    name: "Focus check-in cadence (minutes)",
  });
  await user.clear(field);
  await user.type(field, "-");
  // A browser reads unfinished text such as "-" as an empty value with
  // validity.badInput set (HTML Standard, number state); jsdom never sets badInput.
  Object.defineProperty(field, "validity", {
    configurable: true,
    value: { badInput: true },
  });
  await user.click(screen.getByRole("button", { name: "Save cadence" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Use whole minutes from 5 to 240",
  );
  expect(field).toHaveAttribute("aria-invalid", "true");
  expect(recorder.sent).toEqual([]);
});
