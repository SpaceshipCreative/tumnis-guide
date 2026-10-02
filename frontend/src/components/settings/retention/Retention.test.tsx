// Settings > Retention and the purge dialog (P3-09, SAAS-2, SEC-3): the retention setting
// keeps everything by default and saves a number of days; a purge asks for a reason
// (audited) and stays disabled until one is typed, and names the backup window.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import type { SettingSectionOut } from "../../../api/types.gen";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders } from "../../../test/render";
import { PurgeDialog } from "../connections/PurgeDialog";
import { RetentionSection } from "./RetentionSection";

const CONNECTION = "0190a8f0-0000-7000-8000-00000000c0de";
const PURGE = "0190a8f0-0000-7000-8000-00000000beef";

function handlers(recorder: Recorder) {
  let current: SettingSectionOut = {
    section: "integrations.retention",
    values: { mode: "keep_until_project_purged", days: null },
    secrets_set: [],
    version: null,
  };
  return [
    http.get("*/v1/settings/integrations.retention", () =>
      HttpResponse.json(current),
    ),
    http.put("*/v1/settings/integrations.retention", async ({ request }) => {
      const sent = await recorder.record(request);
      const body = sent.body as { values: Record<string, unknown> };
      current = {
        ...current,
        values: { ...current.values, ...body.values },
        version: (current.version ?? 0) + 1,
      };
      return HttpResponse.json(current);
    }),
    http.post("*/v1/purges", async ({ request }) => {
      await recorder.record(request);
      return HttpResponse.json(
        {
          scope: "connection",
          id: CONNECTION,
          status: "accepted",
          purge_id: PURGE,
        },
        { status: 202 },
      );
    }),
  ];
}

test.fails(
  "[P3-09][SAAS-2] setting and purge dialog need a reason",
  async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder));
    const { user } = renderWithProviders(<RetentionSection />);

    const keep = await screen.findByRole("radio", {
      name: "Keep until the project is purged",
    });
    expect(keep).toBeChecked();
    expect(screen.getByText(/open task/i)).toBeInTheDocument();
    await user.click(
      screen.getByRole("radio", { name: "Delete after a number of days" }),
    );
    const days = screen.getByRole("spinbutton", { name: "Days to keep" });
    await user.clear(days);
    await user.type(days, "30");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        "PUT /v1/settings/integrations.retention",
      ]);
    });
    expect(recorder.sent.at(-1)?.body).toEqual({
      values: { mode: "days", days: 30 },
      version: null,
    });

    const onDone = vi.fn();
    renderWithProviders(
      <PurgeDialog
        scope="connection"
        targetId={CONNECTION}
        name="Work inbox"
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Purge Work inbox",
    });
    expect(within(dialog).getByText(/backups/i)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Purge" });
    expect(confirm).toBeDisabled();
    const reason = within(dialog).getByRole("textbox", { name: "Reason" });
    await user.type(reason, "   ");
    expect(confirm).toBeDisabled();
    await user.clear(reason);
    await user.type(reason, "Mailbox closed");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        "PUT /v1/settings/integrations.retention",
        "POST /v1/purges",
      ]);
    });
    expect(recorder.sent.at(-1)?.body).toEqual({
      scope: "connection",
      id: CONNECTION,
      reason: "Mailbox closed",
    });
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledTimes(1);
    });
  },
);
