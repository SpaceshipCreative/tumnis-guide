import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { zWorkspaceSettingsIn } from "../../api/zod.gen";
import { mockIntlZone } from "../../test/intl";
import { server } from "../../test/msw/server";
import {
  Recorder,
  workspaceHandlers,
  workspaceSettings,
} from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { WorkspaceSection } from "./WorkspaceSection";

async function loadedPicker(): Promise<HTMLSelectElement> {
  const picker = await screen.findByRole<HTMLSelectElement>("combobox", {
    name: "Timezone",
  });
  await waitFor(() => {
    expect(picker).toHaveValue("America/New_York");
  });
  return picker;
}

describe("WorkspaceSection", () => {
  test.fails(
    "[P0-26][FR-3.8] T-P0-26-01 form validates with the generated schema",
    async () => {
      const recorder = new Recorder();
      server.use(...workspaceHandlers(recorder));
      const { user } = renderWithProviders(<WorkspaceSection />);
      await loadedPicker();
      const threshold = screen.getByLabelText(/subtask threshold/i);

      await user.clear(threshold);
      await user.type(threshold, "3");
      await user.click(screen.getByRole("button", { name: "Save" }));

      const message = String(
        zWorkspaceSettingsIn.shape.subtask_threshold_min.safeParse(3).error
          ?.issues[0]?.message,
      );
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(threshold).toHaveAccessibleDescription(message);
      expect(recorder.sent).toEqual([]);

      await user.clear(threshold);
      await user.type(threshold, "30");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        expect(recorder.sent).toHaveLength(1);
      });
      expect(recorder.sent[0]?.body).toMatchObject({
        subtask_threshold_min: 30,
      });
    },
  );

  test.fails(
    "[P0-26][REL-6] T-P0-26-02 timezone picker lists IANA zones and proposes the browser's",
    async () => {
      mockIntlZone("Australia/Sydney");
      server.use(...workspaceHandlers(new Recorder()));
      const { user } = renderWithProviders(<WorkspaceSection />);
      const picker = await loadedPicker();

      const zones = within(picker)
        .getAllByRole<HTMLOptionElement>("option")
        .map((option) => option.value);
      expect(zones).toEqual(
        expect.arrayContaining(["America/New_York", "Australia/Sydney", "UTC"]),
      );

      await user.click(
        screen.getByRole("button", {
          name: "Use this device's zone (Australia/Sydney)",
        }),
      );
      expect(picker).toHaveValue("Australia/Sydney");
      expect(
        screen.queryByRole("button", { name: /Use this device's zone/ }),
      ).not.toBeInTheDocument();
    },
  );

  test.fails(
    "[P0-26][REL-6] T-P0-26-03 saving the timezone sends PUT with version; a 409 shows the current value",
    async () => {
      const recorder = new Recorder();
      const current = workspaceSettings({
        timezone: "Europe/London",
        version: 5,
      });
      server.use(
        ...workspaceHandlers(recorder, workspaceSettings(), () =>
          HttpResponse.json(
            {
              type: "about:blank",
              title: "Stale version",
              status: 409,
              code: "stale_version",
              current,
            },
            { status: 409 },
          ),
        ),
      );
      const { user } = renderWithProviders(<WorkspaceSection />);
      const picker = await loadedPicker();

      await user.selectOptions(picker, "Australia/Sydney");
      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(recorder.sent).toHaveLength(1);
      });
      const [put] = recorder.sent;
      expect(put?.method).toBe("PUT");
      expect(put?.path).toBe("/v1/settings/workspace");
      expect(put?.idempotencyKey).toBeTruthy();
      expect(put?.body).toEqual({
        timezone: "Australia/Sydney",
        subtask_threshold_min: 30,
        version: 4,
      });

      await waitFor(() => {
        expect(picker).toHaveValue("Europe/London");
      });
      expect(screen.getByRole("status")).toHaveTextContent(
        /changed elsewhere/i,
      );
    },
  );
});
