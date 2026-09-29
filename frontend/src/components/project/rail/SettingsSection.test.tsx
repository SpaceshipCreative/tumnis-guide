import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { makeProject } from "../../../test/factories";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders, type Viewport } from "../../../test/render";
import { SettingsSection } from "./SettingsSection";

// Toggling the switch sends one PATCH /v1/projects/{id} with `local_decisions_only` and
// the version read, and the switch shows the saved state.
async function togglesWithOnePatch(viewport: Viewport): Promise<void> {
  const project = {
    ...makeProject({ name: "Beta app", version: 3 }),
    local_decisions_only: false,
  };
  const recorder = new Recorder();
  server.use(
    http.patch("*/v1/projects/:projectId", async ({ request }) => {
      const sent = await recorder.record(request);
      return HttpResponse.json({
        ...project,
        ...(sent.body as Record<string, unknown>),
        version: project.version + 1,
      });
    }),
  );
  const { user } = renderWithProviders(<SettingsSection project={project} />, {
    viewport,
  });

  const toggle = screen.getByRole("switch", { name: "Local decisions only" });
  expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(toggle).toHaveAccessibleDescription(/never sent to Jev/i);
  await user.click(toggle);

  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  const [patch] = recorder.sent;
  expect(patch?.method).toBe("PATCH");
  expect(patch?.path).toBe(`/v1/projects/${project.id}`);
  expect(patch?.idempotencyKey).toBeTruthy();
  expect(patch?.body).toEqual({ local_decisions_only: true, version: 3 });
  await waitFor(() => {
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });
  expect(recorder.sent).toHaveLength(1);
}

describe("SettingsSection", () => {
  test("[P1-02][Data flow rule 6] T-P1-02-16 local decisions toggle sends one PATCH at 375 px", async () => {
    await togglesWithOnePatch("phone");
  });

  test("[P1-02][Data flow rule 6] T-P1-02-16 local decisions toggle sends one PATCH at 1280 px", async () => {
    await togglesWithOnePatch("laptop");
  });
});
